# Patch eventlet to support asynchronous operations
import eventlet
eventlet.monkey_patch()

import logging
import time
import json
import os
import shutil
import tempfile
import subprocess
import signal
from threading import Lock
from datetime import datetime, timedelta
from pytz import timezone
import re
import requests

# Flask-related imports
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Facebook Ads SDK
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.campaign import Campaign

# External libraries
from tqdm import tqdm
from PIL import Image

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed
from routes import file_upload

from clean_up import TempFileCleanup

# Initialize cleanup with a 1-hour expiration and 10-minute interval
temp_cleanup = TempFileCleanup(expiration_time=3600, cleanup_interval=600)
temp_cleanup.start_cleanup()

# Flask app setup
app = Flask(__name__)
app.register_blueprint(file_upload, url_prefix='/file_upload')

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables for tasks and locks
upload_tasks = {}
tasks_lock = Lock()
process_pids = {}
canceled_tasks = set()
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB
VERIFY_TOKEN = "your_secure_random_token"
FACEBOOK_APP_ID = os.getenv("APP_ID")
FACEBOOK_APP_SECRET = os.getenv("APP_SECRET")


#fetch ad_account timezone:
def get_ad_account_timezone(ad_account_id):
    ad_account = AdAccount(ad_account_id).api_get(fields=[AdAccount.Field.timezone_name])
    return ad_account.get('timezone_name')

def convert_to_utc(local_time_str, ad_account_timezone):
    local_tz = timezone(ad_account_timezone)
    local_time = local_tz.localize(datetime.strptime(local_time_str, '%Y-%m-%dT%H:%M:%S'))
    utc_time = local_time.astimezone(timezone('UTC'))
    return utc_time.strftime('%Y-%m-%dT%H:%M:%S')


# Function to create an ad set
def create_ad_set(campaign_id, folder_name, videos, config, task_id):
    check_cancellation(task_id)
    try:
        app_events = config.get('app_events')
        gender = config.get("gender", "All")
        attribution_setting = config.get('attribution_setting', '7d_click')  # Default to '7d_click' if not provided
        event_type = config.get('event_type', 'PURCHASE')  # Default to 'PURCHASE' if not provided
        is_cbo = config.get('is_cbo')
        is_existing_cbo = config.get('is_existing_cbo')
        ad_account_timezone = config.get('ad_account_timezone')

        try:
            age_range = json.loads(config.get("age_range", '[18, 65]'))  # Default to '[18, 65]' if not provided
            age_min = age_range[0]
            age_max = age_range[1]

        except (ValueError, IndexError):
            age_min = 18  # Default value if parsing fails
            age_max = 65  # Default value if parsing fails



        if len(app_events) == 16:
            app_events += ":00"

        app_events = convert_to_utc(app_events, ad_account_timezone)

        start_time = datetime.strptime(app_events, '%Y-%m-%dT%H:%M:%S') if app_events else (datetime.now() + timedelta(days=1)).replace(
            hour=4, minute=0, second=0, microsecond=0
        )

        if gender == "Male":
            gender_value = [1]
        elif gender == "Female":
            gender_value = [2]
        else:
            gender_value = [1, 2]

        # Assign placements based on platform selections
        publisher_platforms = []
        facebook_positions = []
        instagram_positions = []
        messenger_positions = []
        audience_network_positions = []
        # Check for Advantage+ Targeting
        if config.get('targeting_type') == 'Advantage':
            # Use Advantage+ targeting settings here
            ad_set_params = {
                "name": folder_name,
                "campaign_id": campaign_id,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": config.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                "targeting_optimization_type": "TARGETING_OPTIMIZATION_ADVANTAGE_PLUS",
                # Add any other fields required for Advantage+ targeting
                "targeting": {
                    "geo_locations": {"countries": [config["location"]]},
                },
                "start_time": start_time.strftime('%Y-%m-%dT%H:%M:%S'),
                "dynamic_ad_image_enhancement": True,  # Example: enabling dynamic enhancements
                "dynamic_ad_voice_enhancement": True,  # Example: enabling dynamic enhancements
                "promoted_object": {
                    "pixel_id": config["pixel_id"],
                    "custom_event_type": config.get("event_type", "PURCHASE"),
                    "object_store_url": config["object_store_url"] if config["objective"] == "OUTCOME_APP_PROMOTION" else None
                },
                # You may need to adjust or add additional parameters here to match Advantage+ targeting requirements
            }
        else:

            # Check platform selections and corresponding placements
            if config['platforms'].get('facebook'):
                publisher_platforms.append('facebook')
                facebook_positions.extend([
                    'feed'
                ])
                # Add Facebook placements if selected
                if config['placements'].get('profile_feed'):
                    facebook_positions.append('profile_feed')
                if config['placements'].get('marketplace'):
                    facebook_positions.append('marketplace')
                if config['placements'].get('video_feeds'):
                    facebook_positions.append('video_feeds')
                if config['placements'].get('right_column'):
                    facebook_positions.append('right_hand_column')
                if config['placements'].get('stories'):
                    facebook_positions.append('story')
                if config['placements'].get('reels'):
                    facebook_positions.append('facebook_reels')
                if config['placements'].get('in_stream'):
                    facebook_positions.append('instream_video')
                if config['placements'].get('search'):
                    facebook_positions.append('search')
                if config['placements'].get('facebook_reels'):
                    facebook_positions.append('facebook_reels')

            if config['platforms'].get('instagram'):
                publisher_platforms.append('instagram')
                instagram_positions.extend(['stream'])

                # Add Instagram placements if selected
                if config['placements'].get('instagram_feeds'):
                    instagram_positions.append('stream')
                if config['placements'].get('instagram_profile_feed'):
                    instagram_positions.append('profile_feed')
                if config['placements'].get('explore'):
                    instagram_positions.append('explore')
                if config['placements'].get('explore_home'):
                    instagram_positions.append('explore_home')
                if config['placements'].get('instagram_stories'):
                    instagram_positions.append('story')
                if config['placements'].get('instagram_reels'):
                    instagram_positions.append('reels')
                if config['placements'].get('instagram_search'):
                    instagram_positions.append('ig_search')

            if config['platforms'].get('audience_network'):
                publisher_platforms.append('audience_network')
                # Add Audience Network placements if selected
                if config['placements'].get('native_banner_interstitial'):
                    audience_network_positions.append('classic')
                if config['placements'].get('rewarded_videos'):
                    audience_network_positions.append('rewarded_video')
                # When Audience Network is selected, also add Facebook and its feeds
                if 'facebook' not in publisher_platforms:
                    publisher_platforms.append('facebook')
                facebook_positions.extend([
                    'feed',
                ])

            # if config['platforms'].get('messenger'):
            #     publisher_platforms.append('messenger')
            #     # Add Messenger placements if selected
            #     if config['placements'].get('messenger_inbox'):
            #         messenger_positions.append('messenger_home')
            #     if config['placements'].get('messenger_stories'):
            #         messenger_positions.append('story')
            #     if config['placements'].get('messenger_sponsored'):
            #         messenger_positions.append('sponsored_messages')

            ad_set_params = {
                "name": folder_name,
                "campaign_id": campaign_id,
                "billing_event": "IMPRESSIONS",
                "optimization_goal": config.get("optimization_goal", "OFFSITE_CONVERSIONS"),  # Use the optimization goal from config
                "targeting": {
                    "geo_locations": {"countries": config["location"]},  # Updated to support multiple countries
                    "age_min": age_min,
                    "age_max": age_max,
                    "genders": gender_value,
                    "publisher_platforms": publisher_platforms,
                    "facebook_positions": facebook_positions if facebook_positions else None,
                    "instagram_positions": instagram_positions if instagram_positions else None,
                    "messenger_positions": messenger_positions if messenger_positions else None,
                    "audience_network_positions": audience_network_positions if audience_network_positions else None,
                    "custom_audiences":config["custom_audiences"],
                    "flexible_spec": [{"interests": [{"id": spec["value"], "name": spec.get("label", "Unknown Interest")}]} for spec in config.get("flexible_spec", [])],  # Use flexible_spec if present

                },
                "attribution_spec": [
                {
                    "event_type": 'CLICK_THROUGH',  # Use dynamic event type
                    "window_days": int(attribution_setting.split('_')[0].replace('d', ''))
                }
                ],
                "start_time": start_time.strftime('%Y-%m-%dT%H:%M:%S'),
                "dynamic_ad_image_enhancement": False,
                "dynamic_ad_voice_enhancement": False,
                "promoted_object": {
                    "pixel_id": config["pixel_id"],
                    "custom_event_type": event_type,  # Use event type from config with default "PURCHASE"
                    "object_store_url": config["object_store_url"] if config["objective"] == "OUTCOME_APP_PROMOTION" else None
                }
            }

        # Filter out None values from ad_set_params
        ad_set_params = {k: v for k, v in ad_set_params.items() if v is not None}

        if config.get('ad_set_bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP'] or config.get('bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP']:
            bid_amount_cents = int(float(config['bid_amount']) * 100)  # Convert to cents
            ad_set_params["bid_amount"] = bid_amount_cents

        if not is_cbo and not is_existing_cbo:
            if config.get('buying_type') == 'RESERVED':
                ad_set_params["bid_strategy"] = None
                ad_set_params["rf_prediction_id"] = config.get('prediction_id')
            else:
                ad_set_params["bid_strategy"] = config.get('ad_set_bid_strategy', 'LOWEST_COST_WITHOUT_CAP')
            
            if config.get('ad_set_bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP'] or config.get('bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP']:
                bid_amount_cents = int(float(config['bid_amount']) * 100)
                ad_set_params["bid_amount"] = bid_amount_cents

            if config.get('ad_set_budget_optimization') == "DAILY_BUDGET":
                ad_set_params["daily_budget"] = int(float(config['ad_set_budget_value']) * 100)
            elif config.get('ad_set_budget_optimization') == "LIFETIME_BUDGET":
                ad_set_params["lifetime_budget"] = int(float(config['ad_set_budget_value']) * 100)
                end_time = config.get('ad_set_end_time')
                if end_time:
                    if len(end_time) == 16:
                        end_time += ":00"
                    end_time = convert_to_utc(end_time, ad_account_timezone)
                    end_time = datetime.strptime(end_time, '%Y-%m-%dT%H:%M:%S')
                    ad_set_params["end_time"] = end_time.strftime('%Y-%m-%dT%H:%M:%S')
        else:
            if config.get('campaign_budget_optimization') == "LIFETIME_BUDGET":
                end_time = config.get('ad_set_end_time')
                if end_time:
                    if len(end_time) == 16:
                        end_time += ":00"
                    end_time = convert_to_utc(end_time, ad_account_timezone)
                    end_time = datetime.strptime(end_time, '%Y-%m-%dT%H:%M:%S')
                    ad_set_params["end_time"] = end_time.strftime('%Y-%m-%dT%H:%M:%S')

        print("Ad set parameters before creation:", ad_set_params)
        ad_set = AdAccount(config['ad_account_id']).create_ad_set(
            fields=[AdSet.Field.name],
            params=ad_set_params,
        )
        print(f"Created ad set with ID: {ad_set.get_id()}")
        return ad_set
    except Exception as e:
        error_msg = f"Error creating ad set: {e}"
        emit_error(task_id, error_msg)
        return None

# Helper functions for video and image uploads
def upload_video_chunked(video_file, task_id, config):
    check_cancellation(task_id)
    try:
        session = requests.Session()
        headers = {"Authorization": f"Bearer {config['access_token']}"}
        ad_account_id = config['ad_account_id']

        # Step 1: Initiate upload session
        init_endpoint = f"https://graph-video.facebook.com/v19.0/{ad_account_id}/advideos"
        init_params = {
            "upload_phase": "start",
            "file_size": os.path.getsize(video_file),
            "access_token": config['access_token']
        }
        init_response = session.post(init_endpoint, json=init_params, headers=headers).json()
        
        session_id = init_response.get("upload_session_id")
        video_id = init_response.get("video_id")  
        
        if not session_id or not video_id:
            raise Exception("Failed to initialize video upload session or missing video ID")

        print(f"Upload session started. Session ID: {session_id}, Video ID: {video_id}")

        # Step 2: Upload chunks sequentially (NOT in parallel)
        def upload_chunk(start_offset, chunk_data):
            retry_count = 0
            last_offset = start_offset  # Track offset changes

            while retry_count < 3:
                try:
                    time.sleep(0.5)  # Small delay to avoid API overload
                    chunk_endpoint = f"https://graph-video.facebook.com/v19.0/{ad_account_id}/advideos"
                    chunk_params = {
                        "upload_phase": "transfer",
                        "start_offset": start_offset,
                        "upload_session_id": session_id,
                        "access_token": config['access_token']
                    }
                    files = {"video_file_chunk": chunk_data}
                    chunk_response = session.post(chunk_endpoint, params=chunk_params, files=files, headers=headers).json()
                    
                    if "start_offset" in chunk_response:
                        next_offset = chunk_response["start_offset"]
                        print(f"Uploaded chunk. Next start_offset: {next_offset}")

                        # Ensure offset is advancing, otherwise force a reset
                        if next_offset == last_offset:
                            raise Exception(f"Upload stalled at offset {last_offset}, retrying...")

                        return next_offset  # Return updated offset for next chunk
                    else:
                        raise Exception(f"Chunk upload failed: {chunk_response}")

                except Exception as e:
                    print(f"Retry {retry_count+1}/{3} for chunk {start_offset} failed: {e}")
                    retry_count += 1
                    time.sleep(2 * (2 ** retry_count))  # Exponential backoff

            print(f"Chunk upload failed after {3} retries. Aborting upload.")
            return None  # Stop the process if a chunk fails permanently

        # Read video file in chunks and upload sequentially
        with open(video_file, "rb") as f:
            start_offset = 0
            while True:
                chunk_data = f.read(CHUNK_SIZE)
                if not chunk_data:
                    break  # End of file

                # Upload chunk and get new offset
                new_offset = upload_chunk(start_offset, chunk_data)
                if new_offset is None:
                    return None  # Stop if a chunk upload fails permanently

                start_offset = new_offset  # Move to next offset

        # Step 3: Finalize upload
        finish_params = {
            "upload_phase": "finish",
            "upload_session_id": session_id,
            "access_token": config['access_token']
        }
        finish_response = session.post(init_endpoint, data=finish_params, headers=headers).json()

        if finish_response.get("success") is not True:
            if "session expired" in finish_response.get("error", {}).get("message", "").lower():
                print("Session expired. Restarting upload...")
                return upload_video_chunked(video_file, task_id, config)  # Restart upload
            raise Exception(f"Upload failed at finish phase: {finish_response}")

        print(f"Upload completed successfully. Video ID: {video_id}")
        return video_id  
    except Exception as e:
        emit_error(task_id, f"Error uploading video: {e}")
        return None

def upload_video_whole(video_file, task_id, config):
    check_cancellation(task_id)
    try:
        video = AdVideo(parent_id=config['ad_account_id'])
        video[AdVideo.Field.filepath] = video_file
        video.remote_create()
        video_id = video.get_id()
        print(f"Uploaded small video successfully: {video_id}")
        return video_id
    except Exception as e:
        emit_error(task_id, f"Error uploading small video: {e}")
        return None

# **Enhanced Polling Function**
def poll_video_status(video_id, access_token, timeout=600, poll_interval=5):
    """ Polls the Facebook API until the video is processed or timeout occurs. """
    session = requests.Session()
    status_url = f"https://graph-video.facebook.com/v19.0/{video_id}"
    params = {"fields": "status", "access_token": access_token}

    start_time = time.time()
    retry_attempts = 1  # For exponential backoff

    while time.time() - start_time < timeout:
        try:
            response = session.get(status_url, params=params).json()
            status = response.get("status", {}).get("video_status", "unknown")

            if status == "ready":
                print(f"✅ Video {video_id} is ready for use!")
                return True
            elif status in ["processing", "uploading"]:
                print(f"⏳ Video {video_id} still processing... Retrying in {poll_interval} seconds.")
            else:
                print(f"Unexpected video status: {status}")
                return False

        except Exception as e:
            logging.error(f"Error polling video status: {e}")

        time.sleep(poll_interval)
        poll_interval = min(30, poll_interval + 5)  # Exponential backoff (max 30s)
        retry_attempts += 1

    print(f"⚠️ Video {video_id} did not finish processing within {timeout} seconds.")
    return False


# **Final Upload Function**
def upload_video(video_file, task_id, config):
    """Uploads a video and polls the Facebook API until processing is complete."""
    file_size = os.path.getsize(video_file)
    
    # Upload video (chunked or whole depending on size)
    # if file_size > CHUNK_SIZE:
    #     video_id = upload_video_chunked(video_file, task_id, config)
    # else:
    #     video_id = upload_video_whole(video_file, task_id, config)  # You need to define this

    video_id = upload_video_whole(video_file, task_id, config)  # You need to define this


    if not video_id:
        print("Failed to upload video")
        return None

    print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

    # Polling for video processing completion
    success = poll_video_status(video_id, config['access_token'])

    if success:
        print(f"✅ Video {video_id} is fully processed and ready to use.")
        return video_id
    else:
        print(f"⚠️ Video {video_id} failed to process in time.")
        return None


def upload_image(image_file, task_id, config):
    check_cancellation(task_id)
    try:
        image = AdImage(parent_id=config['ad_account_id'])
        image[AdImage.Field.filename] = image_file
        image.remote_create()
        logging.info(f"Uploaded image with hash: {image[AdImage.Field.hash]}")
        return image[AdImage.Field.hash]
    except Exception as e:
        error_msg = f"Error uploading image: {e}"
        emit_error(task_id, error_msg)
        return None

# Function to generate thumbnails for videos
def generate_thumbnail(video_file, thumbnail_file, task_id):
    check_cancellation(task_id)
    command = ['ffmpeg', '-i', video_file, '-ss', '00:00:01.000', '-vframes', '1', '-preset', 'ultrafast', '-threads', '4', '-update', '1', thumbnail_file]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with tasks_lock:
            process_pids.setdefault(task_id, []).append(proc.pid)
        stdout, stderr = proc.communicate()

        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)

    except subprocess.CalledProcessError as e:
        error_msg = f"Error generating thumbnail: {e.cmd} returned non-zero exit status {e.returncode}"
        emit_error(task_id, error_msg)
        raise

def parse_config(config_text):
    config = {}
    lines = config_text.strip().split('\n')
    for line in lines:
        key, value = line.split(':', 1)
        config[key.strip()] = value.strip()
    return config

def convert_webp_to_jpeg(webp_file):
    jpeg_file = os.path.splitext(webp_file)[0] + ".jpg"
    with Image.open(webp_file) as img:
        img.convert("RGB").save(jpeg_file, "JPEG")
    return jpeg_file

def create_ad(ad_set_id, media_file, config, task_id):
    check_cancellation(task_id)
    try:
        ad_format = config.get('ad_format', 'Single image or video')
        if ad_format == 'Single image or video':
            if media_file.lower().endswith('.webp'):
                print("Converting webp to jpeg")
                media_file = convert_webp_to_jpeg(media_file)

            if media_file.lower().endswith(('.jpg', '.png', '.jpeg')):
                print("Images")
                # Image ad logic
                image_hash = upload_image(media_file, task_id, config)
                if not image_hash:
                    print(f"Failed to upload image: {media_file}")
                    return
                
                base_link = config.get('link', 'https://kyronaclinic.com/pages/review-1')
                utm_parameters = config.get('url_parameters', 'utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')

                if utm_parameters and not utm_parameters.startswith('?'):
                    utm_parameters = '?' + utm_parameters
                
                link = base_link + utm_parameters

                call_to_action_type = config.get('call_to_action', 'SHOP_NOW')

                object_story_spec = {
                    "page_id": config.get('facebook_page_id', '102076431877514'),
                    "link_data": {
                        "image_hash": image_hash,
                        "link": link,  # This is the link to your website or product page
                        "message": config.get('ad_creative_primary_text', 'default text'),
                        "name": config.get('ad_creative_headline', 'Your Headline Here'),
                        "description": config.get('ad_creative_description', 'Your Description Here'),
                        "call_to_action": {
                            "type": call_to_action_type,
                            "value": {
                                "link": link
                            }
                        }
                    }
                }

                # Conditionally add instagram_actor_id
                if config.get('instagram_actor_id'):
                    object_story_spec["instagram_actor_id"] = config['instagram_actor_id']
                                
                degrees_of_freedom_spec = {
                    "creative_features_spec": {
                        "standard_enhancements": {
                            "enroll_status": "OPT_OUT"  # explicitly opting out
                        }
                    }
                }

                ad_creative = AdCreative(parent_id=config['ad_account_id'])
                params = {
                    AdCreative.Field.name: "Creative Name",
                    AdCreative.Field.object_story_spec: object_story_spec,
                    AdCreative.Field.degrees_of_freedom_spec: degrees_of_freedom_spec
                }
                ad_creative.update(params)
                ad_creative.remote_create()

                ad = Ad(parent_id=config['ad_account_id'])
                ad[Ad.Field.name] = os.path.splitext(os.path.basename(media_file))[0]
                ad[Ad.Field.adset_id] = ad_set_id
                ad[Ad.Field.creative] = {"creative_id": ad_creative.get_id()}
                ad[Ad.Field.status] = "PAUSED"
                ad.remote_create()

                print(f"Created image ad with ID: {ad.get_id()}")

            else:
                # Video ad logic
                video_path = media_file
                thumbnail_path = f"{os.path.splitext(media_file)[0]}.jpg"

                generate_thumbnail(video_path, thumbnail_path, task_id)
                image_hash = upload_image(thumbnail_path, task_id, config)

                if not image_hash:
                    print(f"Failed to upload thumbnail: {thumbnail_path}")
                    return

                video_id = upload_video(video_path, task_id, config)
                if not video_id:
                    print(f"Failed to upload video: {media_file}")
                    return

                base_link = config.get('link', 'https://kyronaclinic.com/pages/review-1')
                utm_parameters = config.get('url_parameters', 'utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')

                if utm_parameters and not utm_parameters.startswith('?'):
                    utm_parameters = '?' + utm_parameters

                link = base_link + utm_parameters

                call_to_action_type = config.get('call_to_action', 'SHOP_NOW')

                object_story_spec = {
                    "page_id": config.get('facebook_page_id', '102076431877514'),
                    "video_data": {
                        "video_id": video_id,
                        "call_to_action": {
                            "type": call_to_action_type,
                            "value": {
                                "link": link
                            }
                        },
                        "message": config.get('ad_creative_primary_text', 'default text'),
                        "title": config.get('ad_creative_headline', 'No More Neuropathic Foot Pain'),
                        "image_hash": image_hash,
                        "link_description": config.get('ad_creative_description', 'FREE Shipping & 60-Day Money-Back Guarantee')
                    }
                }

                # Conditionally add instagram_actor_id
                if config.get('instagram_actor_id'):
                    object_story_spec["instagram_actor_id"] = config['instagram_actor_id']
                    print("Instagram Actor ID:", config.get('instagram_actor_id'))
                                
                degrees_of_freedom_spec = {
                    "creative_features_spec": {
                        "standard_enhancements": {
                            "enroll_status": "OPT_OUT"  # explicitly opting out
                        }
                    }
                }

                ad_creative = AdCreative(parent_id=config['ad_account_id'])
                params = {
                    AdCreative.Field.name: "Creative Name",
                    AdCreative.Field.object_story_spec: object_story_spec,
                    AdCreative.Field.degrees_of_freedom_spec: degrees_of_freedom_spec
                }
                ad_creative.update(params)
                ad_creative.remote_create()

                ad = Ad(parent_id=config['ad_account_id'])
                ad[Ad.Field.name] = os.path.splitext(os.path.basename(media_file))[0]
                ad[Ad.Field.adset_id] = ad_set_id
                ad[Ad.Field.creative] = {"creative_id": ad_creative.get_id()}
                ad[Ad.Field.status] = "PAUSED"
                ad.remote_create()

                print(f"Created video ad with ID: {ad.get_id()}")

    except TaskCanceledException:
        print(f"Task {task_id} has been canceled during ad creation.")
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError) and e.returncode == -signal.SIGTERM:
            print(f"Task {task_id} process was terminated by signal.")
        else:
            error_msg = f"Error creating ad: {e}"
            emit_error(task_id, error_msg)

def create_carousel_ad(ad_set_id, media_files, config, task_id):
    check_cancellation(task_id)
    try:
        ad_format = config.get('ad_format', 'Carousel')
        if ad_format == 'Carousel':
            carousel_cards = []

            for media_file in media_files:
                if media_file.lower().endswith('.webp'):
                    print("Converting webp to jpeg")
                    media_file = convert_webp_to_jpeg(media_file)

                if media_file.lower().endswith(('.mp4', '.mov', '.avi')):
                    # Video processing
                    video_path = media_file
                    thumbnail_path = f"{os.path.splitext(media_file)[0]}.jpg"

                    generate_thumbnail(video_path, thumbnail_path, task_id)
                    image_hash = upload_image(thumbnail_path, task_id, config)

                    if not image_hash:
                        print(f"Failed to upload thumbnail: {thumbnail_path}")
                        return

                    video_id = upload_video(video_path, task_id, config)
                    if not video_id:
                        print(f"Failed to upload video: {media_file}")
                        return

                    card = {
                        "link": config.get('link', 'https://kyronaclinic.com/pages/review-1'),
                        "video_id": video_id,
                        "call_to_action": {
                            "type": config.get('call_to_action', 'SHOP_NOW'),  # Default to "SHOP_NOW" if not provided
                            "value": {
                                "link": config.get('link', 'https://kyronaclinic.com/pages/review-1')
                            }
                        },
                        "image_hash": image_hash
                    }

                elif media_file.lower().endswith(('.jpg', '.jpeg', '.png')):
                    # Image processing
                    image_hash = upload_image(media_file, task_id, config)
                    if not image_hash:
                        print(f"Failed to upload image: {media_file}")
                        return

                    card = {
                        "link": config.get('link', 'https://kyronaclinic.com/pages/review-1'),
                        "image_hash": image_hash,
                        "call_to_action": {
                            "type": config.get('call_to_action', 'SHOP_NOW'),  # Default to "SHOP_NOW" if not provided
                            "value": {
                                "link": config.get('link', 'https://kyronaclinic.com/pages/review-1')
                            }
                        }
                    }

                else:
                    print(f"Unsupported media file format: {media_file}")
                    continue

                # Add UTM parameters if provided
                utm_parameters = config.get('url_parameters', 'utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')
                if utm_parameters and not utm_parameters.startswith('?'):
                    utm_parameters = '?' + utm_parameters

                card['link'] += utm_parameters
                carousel_cards.append(card)

            object_story_spec = {
                "page_id": config.get('facebook_page_id', '102076431877514'),
                "link_data": {
                    "link": config.get('link', 'https://kyronaclinic.com/pages/review-1'),
                    "child_attachments": carousel_cards,
                    "multi_share_optimized": True,
                    "multi_share_end_card": False,
                    "name": config.get('ad_creative_headline', 'No More Neuropathic Foot Pain'),
                    "description": config.get('ad_creative_description', 'FREE Shipping & 60-Day Money-Back Guarantee'),
                    "caption": config.get('ad_creative_primary_text', 'default text'),
                }
            }

            # Conditionally add instagram_actor_id
            if config.get('instagram_actor_id'):
                object_story_spec["instagram_actor_id"] = config['instagram_actor_id']

            degrees_of_freedom_spec = {
                "creative_features_spec": {
                    "standard_enhancements": {
                        "enroll_status": "OPT_OUT"  # explicitly opting out
                    }
                }
            }

            ad_creative = AdCreative(parent_id=config['ad_account_id'])
            params = {
                AdCreative.Field.name: "Carousel Ad Creative",
                AdCreative.Field.object_story_spec: object_story_spec,
                AdCreative.Field.degrees_of_freedom_spec: degrees_of_freedom_spec
            }
            ad_creative.update(params)
            ad_creative.remote_create()

            ad = Ad(parent_id=config['ad_account_id'])
            ad[Ad.Field.name] = "Carousel Ad"
            ad[Ad.Field.adset_id] = ad_set_id
            ad[Ad.Field.creative] = {"creative_id": ad_creative.get_id()}
            ad[Ad.Field.status] = "PAUSED"
            ad.remote_create()

            print(f"Created carousel ad with ID: {ad.get_id()}")
    except TaskCanceledException:
        print(f"Task {task_id} has been canceled during carousel ad creation.")
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError) and e.returncode == -signal.SIGTERM:
            print(f"Task {task_id} process was terminated by signal.")
        else:
            error_msg = f"Error creating carousel ad: {e}"
            emit_error(task_id, error_msg)
            

def get_all_video_files(directory):
    video_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.mp4', '.mov', '.avi')):
                video_files.append(os.path.join(root, file))
    return video_files

def get_all_image_files(directory):
    image_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                image_files.append(os.path.join(root, file))
    return image_files


if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0',port=5001)