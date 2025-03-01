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
import traceback
import sys

# Patch eventlet to support asynchronous operations
import eventlet
eventlet.monkey_patch()

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

# Flask app setup
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables for tasks and locks
upload_tasks = {}
tasks_lock = Lock()
process_pids = {}
canceled_tasks = set()
CHUNK_SIZE = 20 * 1024 * 1024  # 20MB
MAX_PARALLEL_UPLOADS = 5
VERIFY_TOKEN = "your_secure_random_token"
FACEBOOK_APP_ID = os.getenv("APP_ID")
FACEBOOK_APP_SECRET = os.getenv("APP_SECRET")

# Custom Exception for canceled tasks
class TaskCanceledException(Exception):
    pass

def debug_log(func):
    def wrapper(*args, **kwargs):
        logging.debug(f"Calling function: {func.__name__} with args: {args} kwargs: {kwargs}")
        result = func(*args, **kwargs)
        logging.debug(f"Function {func.__name__} returned: {result}")
        return result
    return wrapper

def log_recursion_depth():
    """Prints the current recursion depth and stack trace when limit is hit."""
    depth = 0
    frame = sys._getframe()
    while frame:
        depth += 1
        frame = frame.f_back
    print(f"[DEBUG] Current recursion depth: {depth}")

    # Capture the stack trace when recursion gets too deep
    if depth > 900:  # Adjust this number if necessary (default recursion limit is 1000)
        print("\n[ERROR] Recursion depth approaching limit!")
        print("=== Stack Trace ===")
        traceback.print_stack()

# Utility function to handle error emission through socket
def emit_error(task_id, message):
    logging.error(f"Raw error message: {message}")  # Log the full raw message for debugging purposes

    # Initialize default title and message
    title = "Error"
    msg = "An unknown error occurred."

    # Step 1: Extract the JSON part from the raw error message using regex
    json_match = re.search(r'Response:\s*(\{.*\})', message, re.DOTALL)
    
    if json_match:
        # Step 2: Parse the extracted JSON part
        try:
            error_data = json.loads(json_match.group(1))

            # Step 3: Extract title and message from the parsed JSON
            title = error_data.get("error", {}).get("error_user_title", "Error")
            print("Title\n")
            print(title)
            msg = error_data.get("error", {}).get("error_user_msg", "An unknown error occurred.")
        except json.JSONDecodeError:
            logging.error("Failed to parse the error JSON from the response.")
    else:
        # If JSON is not found, just use the raw message as the fallback
        msg = message

    # Step 4: Emit the error title and message to the frontend
    socketio.emit('error', {
        'task_id': task_id,
        'title': title,
        'message': msg
    })

    # Emit only the title and message to the frontend
    socketio.emit('error', {'task_id': task_id, 'title': title, 'message': msg})

# Common cancellation check
def check_cancellation(task_id):
    with tasks_lock:
        if task_id in canceled_tasks:
            canceled_tasks.remove(task_id)
            raise TaskCanceledException(f"Task {task_id} has been canceled")

#function to check campaign budget optimization.
def get_campaign_budget_optimization(campaign_id, ad_account_id):
    try:
        campaign = Campaign(campaign_id).api_get(fields=[
            Campaign.Field.name,
            Campaign.Field.effective_status,
            Campaign.Field.daily_budget,
            Campaign.Field.lifetime_budget,
            Campaign.Field.objective

        ])
        
        is_cbo = campaign.get('daily_budget') is not None or campaign.get('lifetime_budget') is not None
        return {
            "name": campaign.get('name'),
            "effective_status": campaign.get('effective_status'),
            "daily_budget": campaign.get('daily_budget'),
            "lifetime_budget": campaign.get('lifetime_budget'),
            "is_campaign_budget_optimization": is_cbo,
            "objective": campaign.get("objective", "OUTCOME_TRAFFIC"),  # Return the campaign objective

        }
    except Exception as e:
        print(f"Error fetching campaign details: {e}")
        return None

# Function to fetch campaign budget optimization status and return a boolean value
def is_campaign_budget_optimized(campaign_id, ad_account_id):
    existing_campaign_budget_optimization = get_campaign_budget_optimization(campaign_id, ad_account_id)
    return existing_campaign_budget_optimization.get('is_campaign_budget_optimization', False)

# Function to create a campaign
def create_campaign(name, objective, budget_optimization, budget_value, bid_strategy, buying_type, task_id, ad_account_id, app_id, app_secret, access_token, is_cbo):
    check_cancellation(task_id)
    try:
        FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v19.0')

        campaign_params = {
            "name": name,
            "objective": objective,
            "special_ad_categories": ["NONE"],
            "buying_type": buying_type,
        }

        # Handling Auction Buying type
        if buying_type == "AUCTION":
            budget_value_cents = int(float(budget_value) * 100)  # Convert to cents
            if is_cbo:
                campaign_params["daily_budget"] = budget_value_cents if budget_optimization == "DAILY_BUDGET" else None
                campaign_params["lifetime_budget"] = budget_value_cents if budget_optimization == "LIFETIME_BUDGET" else None
                campaign_params["bid_strategy"] = bid_strategy

        campaign = AdAccount(ad_account_id).create_campaign(fields=[AdAccount.Field.id], params=campaign_params)
        logging.info(f"Created campaign with ID: {campaign['id']}")
        return campaign['id'], campaign
    except Exception as e:
        error_msg = f"Error creating campaign: {e}"
        emit_error(task_id, error_msg)
        return None, None

#fetch ad_account timezone:
def get_ad_account_timezone(ad_account_id):
    print(f"[DEBUG] Fetching timezone for Ad Account: {ad_account_id}")
    try:
        ad_account = AdAccount(ad_account_id).api_get(fields=['timezone_name'])
        return ad_account.get('timezone_name')
    except Exception as e:
        print(f"[ERROR] Failed to get timezone: {e}")
        return "UTC"  # Return a default value to prevent infinite errors

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
        init_endpoint = f"https://graph-video.facebook.com/v19.0/{ad_account_id}/advideo"
        init_params = {
            "upload_phase": "start",
            "file_size": os.path.getsize(video_file),
            "access_token": config['access_token']
        }
        init_response = session.post(init_endpoint, json=init_params, headers=headers).json()
        session_id = init_response.get("upload_session_id")
        if not session_id:
            raise Exception("Failed to initialize video upload session")
        
        # Step 2: Upload chunks in parallel
        def upload_chunk(start_offset, end_offset, chunk_data):
            chunk_endpoint = f"https://graph-video.facebook.com/v19.0/{ad_account_id}/advideo"
            chunk_params = {
                "upload_phase": "transfer",
                "start_offset": start_offset,
                "upload_session_id": session_id,
                "access_token": config['access_token']
            }
            files = {"video_file_chunk": chunk_data}
            chunk_response = session.post(chunk_endpoint, params=chunk_params, files=files, headers=headers).json()
            return chunk_response

        with open(video_file, "rb") as f:
            chunks = []
            start_offset = 0
            while True:
                chunk_data = f.read(CHUNK_SIZE)
                if not chunk_data:
                    break
                end_offset = start_offset + len(chunk_data)
                chunks.append((start_offset, end_offset, chunk_data))
                start_offset = end_offset

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_UPLOADS) as executor:
            futures = [executor.submit(upload_chunk, start, end, data) for start, end, data in chunks]
            for future in futures:
                future.result()

        # Step 3: Finalize upload
        finish_params = {
            "upload_phase": "finish",
            "upload_session_id": session_id,
            "access_token": config['access_token']
        }
        finish_response = session.post(init_endpoint, json=finish_params, headers=headers).json()
        video_id = finish_response.get("video_id")
        if not video_id:
            raise Exception("Failed to finalize video upload")

        print(f"Uploaded video successfully: {video_id}")
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
    
def upload_video(video_file, task_id, config):
    """Uploads a video and polls the Facebook API until processing is complete."""
    file_size = os.path.getsize(video_file)
    
    # Upload video (chunked or whole depending on size)
    if file_size > CHUNK_SIZE:
        video_id = upload_video_chunked(video_file, task_id, config)
    else:
        video_id = upload_video_whole(video_file, task_id, config)

    if not video_id:
        print("❌ Failed to upload video")
        return None

    print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

    access_token = config['access_token']
    poll_interval = 5  # Check every 5 seconds
    timeout = 600  # 10-minute timeout (adjust if needed)
    start_time = time.time()

    # Poll Facebook API until video processing is done
    while True:
        # Check elapsed time
        elapsed_time = time.time() - start_time
        if elapsed_time > timeout:
            print(f"❌ Timeout: Video {video_id} is still processing after {timeout} seconds.")
            return None

        # Call Facebook API to check processing status
        url = f"https://graph.facebook.com/v20.0/{video_id}?fields=status&access_token={access_token}"
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json()
            processing_status = data.get("status", {}).get("processing_status", "")

            if processing_status == "success":
                print(f"✅ Video {video_id} processing complete.")
                return video_id
            elif processing_status in ["error", "failed"]:
                print(f"❌ Video {video_id} processing failed.")
                return None
        else:
            print(f"⚠️ Error polling video {video_id} status: {response.json()}")

        time.sleep(poll_interval)  # Wait before checking again

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
    command = ['ffmpeg', '-i', video_file, '-ss', '00:00:01.000', '-vframes', '1', '-update', '1', thumbnail_file]
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

def get_video_duration(video_file, task_id):
    check_cancellation(task_id)
    command = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_file
    ]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with tasks_lock:
            if task_id not in process_pids:
                process_pids[task_id] = []
            process_pids[task_id].append(proc.pid)
        stdout, stderr = proc.communicate()
        if proc.returncode == -signal.SIGTERM:
            print(f"Process for task {task_id} was terminated.")
            raise TaskCanceledException(f"Task {task_id} has been canceled")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)
        return float(stdout)
    except subprocess.CalledProcessError as e:
        if e.returncode == -signal.SIGTERM:
            print(f"Process for task {task_id} was terminated by signal.")
            raise TaskCanceledException(f"Task {task_id} has been canceled")
        else:
            print(f"Error getting video duration: {e.cmd} returned non-zero exit status {e.returncode}")
            print(f"Stdout: {e.output.decode()}")
            print(f"Stderr: {e.stderr.decode()}")
            raise



def trim_video(input_file, output_file, duration, task_id):
    check_cancellation(task_id)
    command = [
        'ffmpeg',
        '-i', input_file,
        '-t', str(duration),
        '-c', 'copy',
        output_file
    ]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with tasks_lock:
            if task_id not in process_pids:
                process_pids[task_id] = []
            process_pids[task_id].append(proc.pid)
        stdout, stderr = proc.communicate()
        if proc.returncode == -signal.SIGTERM:
            print(f"Process for task {task_id} was terminated.")
            raise TaskCanceledException(f"Task {task_id} has been canceled")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)
    except subprocess.CalledProcessError as e:
        if e.returncode == -signal.SIGTERM:
            print(f"Process for task {task_id} was terminated by signal.")
            raise TaskCanceledException(f"Task {task_id} has been canceled")
        else:
            print(f"Error trimming video: {e.cmd} returned non-zero exit status {e.returncode}")
            print(f"Stdout: {e.output.decode()}")
            print(f"Stderr: {e.stderr.decode()}")
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
            
def find_campaign_by_id(campaign_id, ad_account_id):
    try:
        campaign = AdAccount(ad_account_id).get_campaigns(
            fields=['name'],
            params={
                'filtering': [{'field': 'id', 'operator': 'EQUAL', 'value': campaign_id}]
            }
        )
        if campaign:
            return campaign_id
        else:
            return None
    except Exception as e:
        print(f"Error finding campaign by ID: {e}")
        return None

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

@app.route('/create_campaign', methods=['POST'])
def handle_create_campaign():
    try:
        config = {}

        def parse_custom_audiences(audience_str):
            """Parses JSON custom audiences string into a list of dicts."""
            try:
                audiences = json.loads(audience_str)
                return [{"id": audience["value"]} for audience in audiences]
            except json.JSONDecodeError as e:
                print(f"Error parsing custom audiences: {e}")
                return []  # Return an empty list if parsing fails

        # Extract and parse request data
        try:
            flexible_spec = json.loads(request.form.get("interests", "[]"))
            print(f"[INFO] Flexible Spec: {flexible_spec}")
        except (TypeError, json.JSONDecodeError):
            flexible_spec = []
            print("[ERROR] Failed to parse flexible_spec")

        custom_audiences_str = request.form.get('custom_audiences', '[]')
        custom_audiences = parse_custom_audiences(custom_audiences_str)
        print(f"[INFO] Custom Audiences: {custom_audiences}")

        campaign_name = request.form.get('campaign_name')
        campaign_id = request.form.get('campaign_id')
        print(f"[INFO] Campaign ID: {campaign_id}")
        upload_folder = request.files.getlist('uploadFolders')

        task_id = request.form.get('task_id')

        # Extracting API keys & IDs
        ad_account_id = request.form.get('ad_account_id', 'act_2945173505586523')
        pixel_id = request.form.get('pixel_id', '466400552489809')
        facebook_page_id = request.form.get('facebook_page_id', '102076431877514')
        app_id = request.form.get('app_id', '314691374966102')
        app_secret = request.form.get('app_secret', 'dummy_secret')
        access_token = request.form.get('access_token', 'dummy_token')
        ad_format = request.form.get('ad_format', 'Single image or video')

        print(f"[INFO] Access Token: {access_token[:10]}... (truncated)")
        print(f"[INFO] App ID: {app_id}, Ad Account ID: {ad_account_id}")

        # Extract campaign settings
        objective = request.form.get('objective', 'OUTCOME_SALES')
        campaign_budget_optimization = request.form.get('campaign_budget_optimization', 'DAILY_BUDGET')
        budget_value = request.form.get('campaign_budget_value', '50.73')
        bid_strategy = request.form.get('campaign_bid_strategy', 'LOWEST_COST_WITHOUT_CAP')
        buying_type = request.form.get('buying_type', 'AUCTION')
        object_store_url = request.form.get('object_store_url', '')
        bid_amount = request.form.get('bid_amount', '0.0')
        is_cbo = request.form.get('isCBO', 'false').lower() == 'true'

        # Receive the JavaScript objects directly
        platforms = request.form.get('platforms')
        placements = request.form.get('placements')
        # Check if the received platforms and placements are in a valid format
        if not isinstance(platforms, dict):
            try:
                platforms = json.loads(platforms)
            except (TypeError, json.JSONDecodeError) as e:
                logging.error(f"Error decoding platforms JSON: {e}")
                logging.error(f"Received platforms JSON: {platforms}")
                return jsonify({"error": "Invalid platforms JSON"}), 400

        if not isinstance(placements, dict):
            try:
                placements = json.loads(placements)
            except (TypeError, json.JSONDecodeError) as e:
                logging.error(f"Error decoding placements JSON: {e}")
                logging.error(f"Received placements JSON: {placements}")
                return jsonify({"error": "Invalid placements JSON"}), 400

        logging.info(f"Platforms after processing: {platforms}")
        logging.info(f"Placements after processing: {placements}")
        FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v20.0')

        ad_account_timezone = get_ad_account_timezone(ad_account_id)


        with tasks_lock:
            upload_tasks[task_id] = True
            process_pids[task_id] = []

        # Prepare config dictionary
        config = {
            'ad_account_id': ad_account_id,
            'facebook_page_id': facebook_page_id,
            'headline': request.form.get('headline', 'No More Neuropathic Foot Pain'),
            'link': request.form.get('destination_url', 'https://kyronaclinic.com/pages/review-1'),
            'utm_parameters': request.form.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}'),
            'object_store_url': object_store_url,
            'budget_value': budget_value,
            'bid_strategy': bid_strategy,
            'location': request.form.get('location', 'GB'),
            'age_range': request.form.get('age_range',),
            'age_range_max': request.form.get('age_range_max', '65'),
            'pixel_id': pixel_id,
            'objective': objective,
            'ad_creative_primary_text': request.form.get('ad_creative_primary_text', ''),
            'ad_creative_headline': request.form.get('ad_creative_headline', 'No More Neuropathic Foot Pain'),
            'ad_creative_description': request.form.get('ad_creative_description', 'FREE Shipping & 60-Day Money-Back Guarantee'),
            'call_to_action': request.form.get('call_to_action', 'SHOP_NOW'),
            'destination_url': request.form.get('destination_url', 'https://kyronaclinic.com/pages/review-1'),
            'app_events': request.form.get('app_events', (datetime.now() + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')),
            'language_customizations': request.form.get('language_customizations', 'en'),
            'url_parameters': request.form.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}'),
            'gender': request.form.get('gender', 'All'),
            'ad_set_budget_optimization': request.form.get('ad_set_budget_optimization', 'DAILY_BUDGET'),
            'ad_set_budget_value': request.form.get('ad_set_budget_value', '50.73'),
            'ad_set_bid_strategy': request.form.get('ad_set_bid_strategy', 'LOWEST_COST_WITHOUT_CAP'),
            'campaign_budget_optimization': request.form.get('campaign_budget_optimization', 'AD_SET_BUDGET_OPTIMIZATION'),
            'ad_format': ad_format,
            'bid_amount': bid_amount,
            'ad_set_end_time': request.form.get('ad_set_end_time', ''),
            'buying_type': request.form.get('buying_type', 'AUCTION'),
            'platforms': platforms,
            'placements': placements,
            'flexible_spec': flexible_spec,  # Include the parsed flexible_spec
            'geo_locations': request.form.get('location'),
            'optimization_goal': request.form.get('performance_goal', 'OFFSITE_CONVERSIONS'),
            'event_type': request.form.get('event_type', 'PURCHASE'),
            'is_cbo': request.form.get('isCBO', 'false').lower() == 'true',
            'custom_audiences': custom_audiences,
            'attribution_setting': request.form.get('attribution_setting', '7d_click'),
            'ad_account_timezone': ad_account_timezone,
            'instagram_actor_id': request.form.get('instagram_account', '')
        }
        
        print("[INFO] Config successfully initialized.")

    
        
        return jsonify({"message": "Config and request data extracted successfully"}), 200

    except RecursionError:
        print("\n[CRITICAL] Recursion limit exceeded!")
        traceback.print_stack()
        return jsonify({"error": "Recursion limit exceeded"}), 500
    except Exception as e:
        print(f"[ERROR] Exception in handle_create_campaign: {e}")
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

@app.route('/cancel_task', methods=['POST'])
def cancel_task():
    try:
        task_id = request.json.get('task_id')
        print(f"Received request to cancel task: {task_id}")
        with tasks_lock:
            if task_id in canceled_tasks:
                print(f"Task {task_id} already marked for cancellation")
            canceled_tasks.add(task_id)
            if task_id in upload_tasks:
                upload_tasks[task_id] = False
                # Kill the PIDs associated with this task
                for pid in process_pids.get(task_id, []):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                process_pids.pop(task_id, None)
                print(f"Task {task_id} set to be canceled")
        return jsonify({"message": "Task cancellation request processed"}), 200
    except Exception as e:
        print(f"Error handling cancel task request: {e}")
        return jsonify({"error": "Internal server error"}), 500
    
@app.route('/get_campaign_budget_optimization', methods=['POST'])
def handle_get_campaign_budget_optimization():
    try:
        data = request.json        
        campaign_id = data.get('campaign_id')
        ad_account_id = data.get('ad_account_id')
        app_id = data.get('app_id')
        app_secret = data.get('app_secret')
        access_token = data.get('access_token')

        if not campaign_id or not ad_account_id or not app_id or not app_secret or not access_token:
            return jsonify({"error": "Campaign ID, Ad Account ID, App ID, App Secret, and Access Token are required"}), 400

        FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v19.0')
        campaign_budget_optimization = is_campaign_budget_optimized(campaign_id, ad_account_id)

        if campaign_budget_optimization is not None:
            return jsonify({"campaign_budget_optimization": campaign_budget_optimization}), 200
        else:
            return jsonify({"error": "Failed to retrieve campaign budget optimization details"}), 500

    except Exception as e:
        logging.error(f"Error in handle_get_campaign_budget_optimization: {e}")
        return jsonify({"error": "Internal server error"}), 500
    
if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0',port=5001)