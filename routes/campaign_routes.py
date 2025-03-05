import logging
import time
import json
import os
import shutil
from datetime import datetime, timedelta


# Flask-related imports
from flask import Blueprint, request, jsonify

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

from flask import Blueprint, request, jsonify
from services import is_campaign_budget_optimized
from services.task_manager import add_task
from services.campaign_service import process_campaign_config
from utils.validators import validate_campaign_request
from utils.error_handler import emit_error

# Create a Blueprint for campaign-related routes
campaign_bp = Blueprint("campaigns", __name__)

@campaign_bp.route("/budget_optimization", methods=["POST"])
def handle_get_campaign_budget_optimization():
    """
    API route to check if a campaign has budget optimization enabled.

    Expects a JSON payload with:
    {
        "campaign_id": "123456789",
        "ad_account_id": "act_123456789"
    }

    Returns:
        200 OK: Campaign budget optimization details
        400 Bad Request: Missing required fields
        500 Internal Server Error: Unexpected failure
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ["campaign_id", "ad_account_id"]
        missing_fields = [field for field in required_fields if field not in data]

        if missing_fields:
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        # Call the service function to check budget optimization
        campaign_budget_optimization = is_campaign_budget_optimized(
            data["campaign_id"], data["ad_account_id"]
        )

        return jsonify({"campaign_budget_optimization": campaign_budget_optimization}), 200

    except Exception as e:
        logging.error(f"Error in handle_get_campaign_budget_optimization: {e}")
        emit_error(f"Error in handle_get_campaign_budget_optimization: {e}")
        return jsonify({"error": "Internal server error"}), 500


@campaign_bp.route('/create_campaign', methods=['POST'])
def handle_create_campaign():
    try:
        is_valid, response, status_code = validate_campaign_request()
        if not is_valid:
            return response, status_code

        # Extract and process campaign configuration
        config = process_campaign_config(request)
        if not config:
            return jsonify({"error": "Failed to process campaign configuration"}), 500

        # Add task using Task Manager
        add_task(config["task_id"])
        config = {}

        def parse_custom_audiences(audience_str):
            try:
                # Parse the JSON string into a list of dicts
                audiences = json.loads(audience_str)
                # Extract only the `value` (which is the `id`)
                return [{"id": audience["value"]} for audience in audiences]
            except json.JSONDecodeError as e:
                print(f"Error parsing custom audiences: {e}")
                return []  # Return an empty list if parsing fails
        try:
            flexible_spec = json.loads(request.form.get("interests", "[]"))
            print(request.form.get("interests", "[]"))
            print(f"Flexible Spec: {flexible_spec}")
        except (TypeError, json.JSONDecodeError):
            flexible_spec = []  # Default to an empty list if parsing fails
            print("Failed to parse flexible_spec")

                
        custom_audiences_str = request.form.get('custom_audiences', '[]')
        custom_audiences = parse_custom_audiences(custom_audiences_str)
        campaign_name = request.form.get('campaign_name')
        campaign_id = request.form.get('campaign_id')
        folder_id = request.form.get("folder_id")
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
        is_cbo = request.form.get('isCBO', 'false').lower() == 'true'
        
        # Receive the JavaScript objects directly
        if request.is_json:
            platforms = request.json.get('platforms', '{}')
        else:
            platforms = request.form.get('platforms', '{}')
        
        if request.is_json:
            placements = request.json.get('placements', '{}')
        else:
            placements = request.form.get('placements', '{}')
            
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

        config = {
            'ad_account_id': ad_account_id,
            'access_token': access_token,
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

        if campaign_id:
            campaign_id = find_campaign_by_id(campaign_id, ad_account_id)
            existing_campaign_budget_optimization = get_campaign_budget_optimization(campaign_id, ad_account_id)
            is_existingCBO = existing_campaign_budget_optimization.get('is_campaign_budget_optimization', False)
            config['is_existing_cbo'] = is_existingCBO
            if not campaign_id:
                logging.error(f"Campaign ID {campaign_id} not found for ad account {ad_account_id}")
                print(campaign_id)
                print(ad_account_id)
                return jsonify({"error": "Campaign ID not found"}), 404
        else:
            print(objective)
            print("Objective")
            campaign_id, campaign = create_campaign(campaign_name, objective, campaign_budget_optimization, budget_value, bid_strategy, buying_type, task_id, ad_account_id, app_id, app_secret, access_token, is_cbo)
            if not campaign_id:
                logging.error(f"Failed to create campaign with name {campaign_name}")
                return jsonify({"error": "Failed to create campaign"}), 500

        # Construct the full path to the folder
        temp_dir = os.path.join("temp_uploads", folder_id)

        # Ensure the folder exists
        if not os.path.exists(temp_dir):
            return jsonify({"error": f"Upload : {folder_id} no longer exists"}), 404

        os.utime(temp_dir, None)
        
        folders = [f for f in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, f))]

        def has_subfolders(folder):
            for item in os.listdir(folder):
                item_path = os.path.join(folder, item)
                if os.path.isdir(item_path):
                    return True
            return False

        total_videos = 0
        total_images = 0
        for folder in folders:
            folder_path = os.path.join(temp_dir, folder)
            total_videos += len(get_all_video_files(folder_path))
            total_images += len(get_all_image_files(folder_path))

        def process_videos(task_id, campaign_id, folders, config, total_videos):
            try:
                socketio.emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_videos}"})
                processed_videos = 0

                with tqdm(total=total_videos, desc="Processing videos") as pbar:
                    last_update_time = time.time()
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
                                        with ThreadPoolExecutor(max_workers=10) as executor:
                                            future_to_video = {executor.submit(create_ad, ad_set.get_id(), video, config, task_id): video for video in video_files}

                                            for future in as_completed(future_to_video):
                                                check_cancellation(task_id)
                                                video = future_to_video[future]
                                                try:
                                                    future.result()
                                                except TaskCanceledException:
                                                    logging.warning(f"Task {task_id} has been canceled during processing video {video}.")
                                                    return
                                                except Exception as e:
                                                    logging.error(f"Error processing video {video}: {e}")
                                                    socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                                finally:
                                                    processed_videos += 1
                                                    pbar.update(1)

                                                    current_time = time.time()
                                                    if current_time - last_update_time >= 1:
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
                                with ThreadPoolExecutor(max_workers=10) as executor:
                                    future_to_video = {executor.submit(create_ad, ad_set.get_id(), video, config, task_id): video for video in video_files}

                                    for future in as_completed(future_to_video):
                                        check_cancellation(task_id)
                                        video = future_to_video[future]
                                        try:
                                            future.result()
                                        except TaskCanceledException:
                                            logging.warning(f"Task {task_id} has been canceled during processing video {video}.")
                                            return
                                        except Exception as e:
                                            logging.error(f"Error processing video {video}: {e}")
                                            socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                        finally:
                                            processed_videos += 1
                                            pbar.update(1)

                                            current_time = time.time()
                                            if current_time - last_update_time >= 0.5:
                                                socketio.emit('progress', {'task_id': task_id, 'progress': processed_videos / total_videos * 100, 'step': f"{processed_videos}/{total_videos}"})
                                                last_update_time = current_time

                            elif ad_format == 'Carousel':
                                create_carousel_ad(ad_set.get_id(), video_files, config, task_id)

                socketio.emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_videos}/{total_videos}"})
                socketio.emit('task_complete', {'task_id': task_id})
            except TaskCanceledException:
                logging.warning(f"Task {task_id} has been canceled during video processing.")
            except Exception as e:
                logging.error(f"Error in processing videos: {e}")
                socketio.emit('error', {'task_id': task_id, 'message': str(e)})
            finally:
                with tasks_lock:
                    process_pids.pop(task_id, None)
                shutil.rmtree(temp_dir, ignore_errors=True)

        def process_images(task_id, campaign_id, folders, config, total_images):
            try:
                socketio.emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_images}"})
                processed_images = 0

                with tqdm(total=total_images, desc="Processing images") as pbar:
                    last_update_time = time.time()
                    for folder in folders:
                        check_cancellation(task_id)
                        folder_path = os.path.join(temp_dir, folder)

                        if has_subfolders(folder_path):
                            for subfolder in os.listdir(folder_path):
                                subfolder_path = os.path.join(folder_path, subfolder)
                                if os.path.isdir(subfolder_path):
                                    image_files = get_all_image_files(subfolder_path)
                                    if not image_files:
                                        continue

                                    ad_set = create_ad_set(campaign_id, subfolder, image_files, config, task_id)
                                    if not ad_set:
                                        continue

                                    if config['ad_format'] == 'Single image or video':
                                        with ThreadPoolExecutor(max_workers=10) as executor:
                                            future_to_image = {executor.submit(create_ad, ad_set.get_id(), image, config, task_id): image for image in image_files}

                                            for future in as_completed(future_to_image):
                                                check_cancellation(task_id)
                                                image = future_to_image[future]
                                                try:
                                                    future.result()
                                                except TaskCanceledException:
                                                    logging.warning(f"Task {task_id} has been canceled during processing image {image}.")
                                                    return
                                                except Exception as e:
                                                    logging.error(f"Error processing image {image}: {e}")
                                                    socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                                finally:
                                                    processed_images += 1
                                                    pbar.update(1)

                                                    current_time = time.time()
                                                    if current_time - last_update_time >= 1:
                                                        socketio.emit('progress', {'task_id': task_id, 'progress': processed_images / total_images * 100, 'step': f"{processed_images}/{total_images}"})
                                                        last_update_time = current_time

                                    elif config['ad_format'] == 'Carousel':
                                        create_carousel_ad(ad_set.get_id(), image_files, config, task_id)

                        else:
                            image_files = get_all_image_files(folder_path)
                            if not image_files:
                                continue

                            ad_set = create_ad_set(campaign_id, folder, image_files, config, task_id)
                            if not ad_set:
                                continue

                            if config['ad_format'] == 'Single image or video':
                                with ThreadPoolExecutor(max_workers=10) as executor:
                                    future_to_image = {executor.submit(create_ad, ad_set.get_id(), image, config, task_id): image for image in image_files}

                                    for future in as_completed(future_to_image):
                                        check_cancellation(task_id)
                                        image = future_to_image[future]
                                        try:
                                            future.result()
                                        except TaskCanceledException:
                                            logging.warning(f"Task {task_id} has been canceled during processing image {image}.")
                                            return
                                        except Exception as e:
                                            logging.error(f"Error processing image {image}: {e}")
                                            socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                        finally:
                                            processed_images += 1
                                            pbar.update(1)

                                            current_time = time.time()
                                            if current_time - last_update_time >= 0.5:
                                                socketio.emit('progress', {'task_id': task_id, 'progress': processed_images / total_images * 100, 'step': f"{processed_images}/{total_images}"})
                                                last_update_time = current_time

                            elif config['ad_format'] == 'Carousel':
                                create_carousel_ad(ad_set.get_id(), image_files, config, task_id)

                socketio.emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_images}/{total_images}"})
                socketio.emit('task_complete', {'task_id': task_id})
            except TaskCanceledException:
                logging.warning(f"Task {task_id} has been canceled during image processing.")
            except Exception as e:
                logging.error(f"Error in processing images: {e}")
                socketio.emit('error', {'task_id': task_id, 'message': str(e)})
            finally:
                with tasks_lock:
                    process_pids.pop(task_id, None)
                shutil.rmtree(temp_dir, ignore_errors=True)
        
        def process_mixed_media(task_id, campaign_id, folders, config, total_videos, total_images):
            try:
                total_files = total_videos + total_images
                socketio.emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_files}"})
                processed_files = 0

                with tqdm(total=total_files, desc="Processing mixed media") as pbar:
                    last_update_time = time.time()
                    for folder in folders:
                        check_cancellation(task_id)
                        folder_path = os.path.join(temp_dir, folder)

                        # Check if the folder contains subfolders
                        if has_subfolders(folder_path):
                            for subfolder in os.listdir(folder_path):
                                subfolder_path = os.path.join(folder_path, subfolder)
                                if os.path.isdir(subfolder_path):
                                    video_files = get_all_video_files(subfolder_path)
                                    image_files = get_all_image_files(subfolder_path)
                                    media_files = video_files + image_files

                                    if media_files:
                                        # Create an ad set for each subfolder
                                        ad_set = create_ad_set(campaign_id, subfolder, media_files, config, task_id)
                                        if not ad_set:
                                            continue

                                        if config['ad_format'] == 'Single image or video':
                                            with ThreadPoolExecutor(max_workers=10) as executor:
                                                future_to_media = {executor.submit(create_ad, ad_set.get_id(), media, config, task_id): media for media in media_files}

                                                for future in as_completed(future_to_media):
                                                    check_cancellation(task_id)
                                                    media = future_to_media[future]
                                                    try:
                                                        future.result()
                                                    except TaskCanceledException:
                                                        logging.warning(f"Task {task_id} has been canceled during processing media {media}.")
                                                        return
                                                    except Exception as e:
                                                        logging.error(f"Error processing media {media}: {e}")
                                                        socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                                    finally:
                                                        processed_files += 1
                                                        pbar.update(1)

                                                        current_time = time.time()
                                                        if current_time - last_update_time >= 0.5:
                                                            socketio.emit('progress', {'task_id': task_id, 'progress': processed_files / total_files * 100, 'step': f"{processed_files}/{total_files}"})
                                                            last_update_time = current_time

                                        elif config['ad_format'] == 'Carousel':
                                            create_carousel_ad(ad_set.get_id(), media_files, config, task_id)

                        else:
                            # Process the folder if no subfolders exist
                            video_files = get_all_video_files(folder_path)
                            image_files = get_all_image_files(folder_path)
                            media_files = video_files + image_files

                            if media_files:
                                # Create an ad set for the folder
                                ad_set = create_ad_set(campaign_id, folder, media_files, config, task_id)
                                if not ad_set:
                                    continue

                                if config['ad_format'] == 'Single image or video':
                                    with ThreadPoolExecutor(max_workers=10) as executor:
                                        future_to_media = {executor.submit(create_ad, ad_set.get_id(), media, config, task_id): media for media in media_files}

                                        for future in as_completed(future_to_media):
                                            check_cancellation(task_id)
                                            media = future_to_media[future]
                                            try:
                                                future.result()
                                            except TaskCanceledException:
                                                logging.warning(f"Task {task_id} has been canceled during processing media {media}.")
                                                return
                                            except Exception as e:
                                                logging.error(f"Error processing media {media}: {e}")
                                                socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                            finally:
                                                processed_files += 1
                                                pbar.update(1)

                                                current_time = time.time()
                                                if current_time - last_update_time >= 0.5:
                                                    socketio.emit('progress', {'task_id': task_id, 'progress': processed_files / total_files * 100, 'step': f"{processed_files}/{total_files}"})
                                                    last_update_time = current_time

                                elif config['ad_format'] == 'Carousel':
                                    create_carousel_ad(ad_set.get_id(), media_files, config, task_id)

                socketio.emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_files}/{total_files}"})
                socketio.emit('task_complete', {'task_id': task_id})

            except TaskCanceledException:
                logging.warning(f"Task {task_id} has been canceled during mixed media processing.")
            except Exception as e:
                logging.error(f"Error in processing mixed media: {e}")
                socketio.emit('error', {'task_id': task_id, 'message': str(e)})
            finally:
                with tasks_lock:
                    process_pids.pop(task_id, None)
                shutil.rmtree(temp_dir, ignore_errors=True)

        
        


        # Call the appropriate processing function based on media types
        if total_videos > 0 and total_images > 0:
            socketio.start_background_task(target=process_mixed_media, task_id=task_id, campaign_id=campaign_id, folders=folders, config=config, total_videos=total_videos, total_images=total_images)
        elif total_videos > 0:
            socketio.start_background_task(target=process_videos, task_id=task_id, campaign_id=campaign_id, folders=folders, config=config, total_videos=total_videos)
        elif total_images > 0:
            socketio.start_background_task(target=process_images, task_id=task_id, campaign_id=campaign_id, folders=folders, config=config, total_images=total_images)

        return jsonify({"message": "Campaign processing started", "task_id": task_id})

    except Exception as e:
        logging.error(f"Error in handle_create_campaign: {e}")
        return jsonify({"error": "Internal server error"}), 500
