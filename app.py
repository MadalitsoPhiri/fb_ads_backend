import logging
import time
import eventlet
import json

# Now monkey-patch with eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify
import shutil
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os
import tempfile
import subprocess
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage
from threading import Lock
import signal
from tqdm import tqdm
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

upload_tasks = {}
tasks_lock = Lock()
process_pids = {}
canceled_tasks = set()

class TaskCanceledException(Exception):
    pass

def create_campaign(name, objective, campaign_budget_optimization, budget_value, bid_strategy, buying_type, task_id, ad_account_id, app_id, app_secret, access_token):
    check_cancellation(task_id)
    try:
        FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v19.0')
        
        campaign_params = {
            "name": name,
            "objective": objective,
            "special_ad_categories": ["NONE"],
            "buying_type": buying_type,
        }

        if buying_type == "AUCTION":
            budget_value_cents = int(float(budget_value) * 100)  # Convert dollars to cents
            campaign_params["daily_budget"] = budget_value_cents if campaign_budget_optimization == "DAILY_BUDGET" else None
            campaign_params["lifetime_budget"] = budget_value_cents if campaign_budget_optimization == "LIFETIME_BUDGET" else None
            campaign_params["bid_strategy"] = bid_strategy if campaign_budget_optimization != "AD_SET_BUDGET_OPTIMIZATION" else None
            
        campaign = AdAccount(ad_account_id).create_campaign(
            fields=[AdAccount.Field.id],
            params=campaign_params,
        )

        print(f"Created campaign with ID: {campaign['id']}")
        return campaign['id'], campaign
    except Exception as e:
        print(f"Error creating campaign: {e}")
        return None, None

def create_ad_set(campaign_id, folder_name, videos, config, task_id):
    check_cancellation(task_id)
    try:
        app_events = config.get('app_events')
        gender = config.get("gender", "All")
        if len(app_events) == 16:
            app_events += ":00"
        start_time = datetime.strptime(app_events, '%Y-%m-%dT%H:%M:%S') if app_events else (datetime.now() + timedelta(days=1)).replace(
            hour=4, minute=0, second=0, microsecond=0
        )

        gender_value = [1, 2] if gender == "All" else [1] if gender == "Male" else [2]

        publisher_platforms = []
        facebook_positions = []
        instagram_positions = []
        messenger_positions = []
        audience_network_positions = []

        # Assign placements based on platform selections
        if config['platforms'].get('facebook'):
            publisher_platforms.append('facebook')
            if config['placements'].get('feeds'):
                facebook_positions.extend([
                    'feed', 
                    'profile_feed', 
                    'marketplace', 
                    'video_feeds', 
                    'right_hand_column', 
                    'business_explore'
                ])
            if config['placements'].get('stories'):
                facebook_positions.extend(['story', 'facebook_reels'])
            if config['placements'].get('instream_video'):
                facebook_positions.extend(['instream_video', 'ads_on_facebook_reels'])
            if config['placements'].get('search'):
                facebook_positions.append('search')
        
        if config['platforms'].get('instagram'):
            publisher_platforms.append('instagram')
            if config['placements'].get('feeds'):
                instagram_positions.extend(['stream', 'profile_feed'])
            if config['placements'].get('stories'):
                instagram_positions.extend(['story', 'reels'])
            if config['placements'].get('explore'):
                instagram_positions.extend(['explore', 'explore_home'])
            if config['placements'].get('search'):
                instagram_positions.append('ig_search')
        
        if config['platforms'].get('messenger'):
            publisher_platforms.append('messenger')
            if config['placements'].get('stories'):
                messenger_positions.append('story')
            if config['placements'].get('messages'):
                messenger_positions.extend(['messenger_home', 'sponsored_messages'])

        if config['platforms'].get('audience_network'):
            publisher_platforms.append('audience_network')
            if config['placements'].get('apps_sites'):
                audience_network_positions.extend(['classic', 'rewarded_video'])

        ad_set_params = {
            "name": folder_name,
            "campaign_id": campaign_id,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "targeting": {
                "geo_locations": {"countries": [config["location"]]},
                "age_min": int(config["age_range_min"]),
                "age_max": int(config["age_range_max"]),
                "genders": gender_value,
                "publisher_platforms": publisher_platforms,
                "facebook_positions": facebook_positions if facebook_positions else None,
                "instagram_positions": instagram_positions if instagram_positions else None,
                "messenger_positions": messenger_positions if messenger_positions else None,
                "audience_network_positions": audience_network_positions if audience_network_positions else None
            },
            "start_time": start_time.strftime('%Y-%m-%dT%H:%M:%S'),
            "dynamic_ad_image_enhancement": False,
            "dynamic_ad_voice_enhancement": False,
            "promoted_object": {
                "pixel_id": config["pixel_id"],
                "custom_event_type": "PURCHASE",
                "object_store_url": config["object_store_url"] if config["objective"] == "OUTCOME_APP_PROMOTION" else None
            }
        }

        ad_set_params = {k: v for k, v in ad_set_params.items() if v is not None}

        if config.get('ad_set_bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP'] or config.get('bid_strategy') in ['COST_CAP', 'LOWEST_COST_WITH_BID_CAP']:
            bid_amount_cents = int(float(config['bid_amount']) * 100)
            ad_set_params["bid_amount"] = bid_amount_cents

        if config.get('campaign_budget_optimization') == 'AD_SET_BUDGET_OPTIMIZATION':
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
                    end_time = datetime.strptime(end_time, '%Y-%m-%dT%H:%M:%S')
                    ad_set_params["end_time"] = end_time.strftime('%Y-%m-%dT%H:%M:%S')
        else:
            if config.get('campaign_budget_optimization') == "LIFETIME_BUDGET":
                end_time = config.get('ad_set_end_time')
                if end_time:
                    if len(end_time) == 16:
                        end_time += ":00"
                    end_time = datetime.strptime(end_time, '%Y-%m-%dT%H:%M:%S')
                    ad_set_params["end_time"] = end_time.strftime('%Y-%m-%dT%H:%M:%S')

        ad_set = AdAccount(config['ad_account_id']).create_ad_set(
            fields=[AdSet.Field.name],
            params=ad_set_params,
        )
        return ad_set
    except Exception as e:
        return None

def upload_video(video_file, task_id, config):
    check_cancellation(task_id)
    try:
        video = AdVideo(parent_id=config['ad_account_id'])
        video[AdVideo.Field.filepath] = video_file
        video.remote_create()
        print(f"Uploaded video with ID: {video.get_id()}")
        return video.get_id()
    except Exception as e:
        print(f"Error uploading video: {e}")
        return None

def upload_image(image_file, task_id, config):
    check_cancellation(task_id)
    try:
        image = AdImage(parent_id=config['ad_account_id'])
        image[AdImage.Field.filename] = image_file
        image.remote_create()
        print(f"Uploaded image with hash: {image[AdImage.Field.hash]}")
        return image[AdImage.Field.hash]
    except Exception as e:
        print(f"Error uploading image: {e}")
        return None

def generate_thumbnail(video_file, thumbnail_file, task_id):
    check_cancellation(task_id)
    command = [
        'ffmpeg',
        '-i', video_file,
        '-ss', '00:00:01.000',
        '-vframes', '1',
        '-update', '1',  # Ensure single image output
        thumbnail_file
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
            print(f"Error generating thumbnail: {e.cmd} returned non-zero exit status {e.returncode}")
            print(f"Stdout: {e.output.decode()}")
            print(f"Stderr: {e.stderr.decode()}")
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

def create_ad(ad_set_id, video_file, config, task_id):
    check_cancellation(task_id)
    try:
        ad_format = config.get('ad_format', 'Single image or video')
        if ad_format == 'Single image or video':
            
            video_path = video_file
            thumbnail_path = f"{os.path.splitext(video_file)[0]}.jpg"
            
            generate_thumbnail(video_path, thumbnail_path, task_id)
            image_hash = upload_image(thumbnail_path, task_id, config)
            
            if not image_hash:
                print(f"Failed to upload thumbnail: {thumbnail_path}")
                return

            max_duration = 240 * 60  # 240 minutes
            video_duration = get_video_duration(video_path, task_id)
            if video_duration > max_duration:
                trimmed_video_path = f"./trimmed_{os.path.basename(video_file)}"
                trim_video(video_path, trimmed_video_path, max_duration, task_id)
                video_path = trimmed_video_path

            video_id = upload_video(video_path, task_id, config)
            if not video_id:
                print(f"Failed to upload video: {video_file}")
                return
            
            base_link = config.get('link', 'https://kyronaclinic.com/pages/review-1')
            utm_parameters = config.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')
            link = base_link + utm_parameters

            call_to_action_type = config.get('call_to_action', 'SHOP_NOW')  # Default to "SHOP_NOW" if not provided

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
            degrees_of_freedom_spec = {
                "creative_features_spec": {
                    "standard_enhancements": {
                        "enroll_status": "OPT_OUT"
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
            ad[Ad.Field.name] = os.path.splitext(os.path.basename(video_file))[0]
            ad[Ad.Field.adset_id] = ad_set_id
            ad[Ad.Field.creative] = {"creative_id": ad_creative.get_id()}
            ad[Ad.Field.status] = "PAUSED"
            ad.remote_create()
            
            os.remove(thumbnail_path)
            
            print(f"Created ad with ID: {ad.get_id()}")
    except TaskCanceledException:
        print(f"Task {task_id} has been canceled during ad creation.")
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError) and e.returncode == -signal.SIGTERM:
            print(f"Task {task_id} process was terminated by signal.")
        else:
            print(f"Error creating ad: {e}")
            socketio.emit('error', {'task_id': task_id, 'message': str(e)})

def create_carousel_ad(ad_set_id, media_files, config, task_id):
    check_cancellation(task_id)
    try:
        ad_format = config.get('ad_format', 'Carousel')
        if ad_format == 'Carousel':
            carousel_cards = []

            for media_file in media_files:
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
                utm_parameters = config.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')
                link = base_link + utm_parameters

                call_to_action_type = config.get('call_to_action', 'SHOP_NOW')  # Default to "SHOP_NOW" if not provided

                card = {
                    "link": link,
                    "video_id": video_id,
                    "call_to_action": {
                        "type": call_to_action_type,
                        "value": {
                            "link": link
                        }
                    },
                    "image_hash": image_hash
                }

                carousel_cards.append(card)

            object_story_spec = {
                "page_id": config.get('facebook_page_id', '102076431877514'),
                "link_data": {
                    "link": base_link,
                    "child_attachments": carousel_cards,
                    "multi_share_optimized": True,
                    "multi_share_end_card": False,
                    "name": config.get('ad_creative_headline', 'No More Neuropathic Foot Pain'),
                    "description": config.get('ad_creative_description', 'FREE Shipping & 60-Day Money-Back Guarantee'),
                    "caption": config.get('ad_creative_primary_text', 'default text'),
                }
            }

            ad_creative = AdCreative(parent_id=config['ad_account_id'])
            params = {
                AdCreative.Field.name: "Carousel Ad Creative",
                AdCreative.Field.object_story_spec: object_story_spec
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
            print(f"Error creating carousel ad: {e}")
            socketio.emit('error', {'task_id': task_id, 'message': str(e)})
            
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

def check_cancellation(task_id):
    with tasks_lock:
        if task_id in canceled_tasks:
            canceled_tasks.remove(task_id)
            raise TaskCanceledException(f"Task {task_id} has been canceled")

def get_all_video_files(directory):
    video_files = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.mp4', '.mov', '.avi')):
                video_files.append(os.path.join(root, file))
    return video_files

@app.route('/create_campaign', methods=['POST'])
def handle_create_campaign():
    campaign_name = request.form.get('campaign_name')
    campaign_id = request.form.get('campaign_id')
    upload_folder = request.files.getlist('uploadFolders')
    task_id = request.form.get('task_id')

    ad_account_id = request.form.get('ad_account_id', 'act_2945173505586523')
    pixel_id = request.form.get('pixel_id', '466400552489809')
    facebook_page_id = request.form.get('facebook_page_id', '102076431877514')
    app_id = request.form.get('app_id', '314691374966102')
    app_secret = request.form.get('app_secret', '88d92443cfcfc3922cdea79b384a116e')
    access_token = request.form.get('access_token', 'EAAEeNcueZAVYBO0NvEUMo378SikOh70zuWuWgimHhnE5Vk7ye8sZCaRtu9qQGWNDvlBZBBnZAT6HCuDlNc4OeOSsdSw5qmhhmtKvrWmDQ8ZCg7a1BZAM1NS69YmtBJWGlTwAmzUB6HuTmb3Vz2r6ig9Xz9ZADDDXauxFCry47Fgh51yS1JCeo295w2V')
    ad_format = request.form.get('ad_format', 'Single image or video')

    objective = request.form.get('objective', 'OUTCOME_SALES')
    campaign_budget_optimization = request.form.get('campaign_budget_optimization', 'DAILY_BUDGET')
    budget_value = request.form.get('campaign_budget_value', '50.73')
    bid_strategy = request.form.get('campaign_bid_strategy', 'LOWEST_COST_WITHOUT_CAP')
    buying_type = request.form.get('buying_type', 'AUCTION')
    object_store_url = request.form.get('object_store_url', '')
    bid_amount = request.form.get('bid_amount', '0.0')

    platforms_str = request.form.get('platforms', '{}')
    placements_str = request.form.get('placements', '{}')

    try:
        platforms = json.loads(platforms_str)
    except json.JSONDecodeError as e:
        print(f"Error decoding platforms JSON: {e}")
        platforms = {}

    try:
        placements = json.loads(placements_str)
    except json.JSONDecodeError as e:
        print(f"Error decoding placements JSON: {e}")
        placements = {}

    print(platforms)
    print(placements)

    with tasks_lock:
        upload_tasks[task_id] = True
        process_pids[task_id] = []

    if campaign_id:
        campaign_id = find_campaign_by_id(campaign_id, ad_account_id)
        if not campaign_id:
            return jsonify({"error": "Campaign ID not found"}), 404
    else:
        campaign_id, campaign = create_campaign(campaign_name, objective, campaign_budget_optimization, budget_value, bid_strategy, buying_type, task_id, ad_account_id, app_id, app_secret, access_token)
        if not campaign_id:
            return jsonify({"error": "Failed to create campaign"}), 500

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
        'age_range_min': request.form.get('age_range_min', '30'),
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
        'platforms': platforms,  # Include platforms in config
        'placements': placements  # Include placements in config
    }

    temp_dir = tempfile.mkdtemp()
    for file in upload_folder:
        file_path = os.path.join(temp_dir, file.filename)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if not file.filename.startswith('.'):  # Skip hidden files like .DS_Store
            file.save(file_path)
    
    folders = [f for f in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, f))]

    # Check if any folder has subfolders
    def has_subfolders(folder):
        for item in os.listdir(folder):
            item_path = os.path.join(folder, item)
            if os.path.isdir(item_path):
                return True
        return False

    total_videos = 0
    for folder in folders:
        folder_path = os.path.join(temp_dir, folder)
        total_videos += len(get_all_video_files(folder_path))

    def process_videos(task_id, campaign_id, folders, config, total_videos):
        try:
            socketio.emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_videos}"})
            processed_videos = 0

            with tqdm(total=total_videos, desc="Processing videos") as pbar:
                last_update_time = time.time()  # Initialize the last update time
                for folder in folders:
                    check_cancellation(task_id)
                    folder_path = os.path.join(temp_dir, folder)

                    if has_subfolders(folder_path):
                        for subfolder in os.listdir(folder_path):
                            subfolder_path = os.path.join(folder_path, subfolder)
                            if os.path.isdir(subfolder_path):
                                video_files = get_all_video_files(subfolder_path)
                                if not video_files:
                                    continue

                                ad_set = create_ad_set(campaign_id, subfolder, video_files, config, task_id)
                                if not ad_set:
                                    continue

                                if ad_format == 'Single image or video':
                                    with ThreadPoolExecutor(max_workers=5) as executor:
                                        future_to_video = {executor.submit(create_ad, ad_set.get_id(), video, config, task_id): video for video in video_files}

                                        for future in as_completed(future_to_video):
                                            check_cancellation(task_id)
                                            video = future_to_video[future]
                                            try:
                                                future.result()
                                            except TaskCanceledException:
                                                print(f"Task {task_id} has been canceled during processing video {video}.")
                                                return
                                            except Exception as e:
                                                print(f"Error processing video {video}: {e}")
                                                socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                            finally:
                                                processed_videos += 1
                                                pbar.update(1)
                                                
                                                # Periodically emit progress updates
                                                current_time = time.time()
                                                if current_time - last_update_time >= 1:  # Update every second
                                                    socketio.emit('progress', {'task_id': task_id, 'progress': processed_videos / total_videos * 100, 'step': f"{processed_videos}/{total_videos}"})
                                                    last_update_time = current_time

                                elif ad_format == 'Carousel':
                                    create_carousel_ad(ad_set.get_id(), video_files, config, task_id)

                    else:
                        video_files = get_all_video_files(folder_path)
                        if not video_files:
                            continue

                        ad_set = create_ad_set(campaign_id, folder, video_files, config, task_id)
                        if not ad_set:
                            continue

                        if ad_format == 'Single image or video':
                            with ThreadPoolExecutor(max_workers=5) as executor:
                                future_to_video = {executor.submit(create_ad, ad_set.get_id(), video, config, task_id): video for video in video_files}

                                for future in as_completed(future_to_video):
                                    check_cancellation(task_id)
                                    video = future_to_video[future]
                                    try:
                                        future.result()
                                    except TaskCanceledException:
                                        print(f"Task {task_id} has been canceled during processing video {video}.")
                                        return
                                    except Exception as e:
                                        print(f"Error processing video {video}: {e}")
                                        socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                    finally:
                                        processed_videos += 1
                                        pbar.update(1)
                                        
                                        # Periodically emit progress updates
                                        current_time = time.time()
                                        if current_time - last_update_time >= 0.5:  # Update every 0.5 seconds
                                            socketio.emit('progress', {'task_id': task_id, 'progress': processed_videos / total_videos * 100, 'step': f"{processed_videos}/{total_videos}"})
                                            last_update_time = current_time

                        elif ad_format == 'Carousel':
                            create_carousel_ad(ad_set.get_id(), video_files, config, task_id)

            socketio.emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_videos}/{total_videos}"})
            socketio.emit('task_complete', {'task_id': task_id})
        except TaskCanceledException:
            print(f"Task {task_id} has been canceled during video processing.")
        except Exception as e:
            print(f"Error in processing videos: {e}")
            socketio.emit('error', {'task_id': task_id, 'message': str(e)})
        finally:
            with tasks_lock:
                process_pids.pop(task_id, None)
            # Clean up temporary files
            shutil.rmtree(temp_dir, ignore_errors=True)

    socketio.start_background_task(target=process_videos, task_id=task_id, campaign_id=campaign_id, folders=folders, config=config, total_videos=total_videos)

    return jsonify({"message": "Campaign processing started", "task_id": task_id})


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

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0',port=5001)
