import os
import subprocess
import signal

# Facebook Ads SDK
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad

# Task Management & Error Handling
from services.task_manager import check_cancellation, TaskCanceledException
from services.upload_service import upload_image, upload_video
from utils.error_handler import emit_error

def create_ad(ad_set_id, media_file, config, task_id):
    check_cancellation(task_id)
    try:
        ad_format = config.get('ad_format', 'Single image or video')
        if ad_format == 'Single image or video':
            if media_file["media_file"].lower().endswith(('.jpg', '.png', '.jpeg', '.webp')):
                # Image ad logic
                image_hash = upload_image(media_file, task_id, config)
                if not image_hash:
                    print(f"Failed to upload image: {media_file}")
                    return
                
                base_link = config.get('link', '')
                utm_parameters = config.get('url_parameters', 'utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')

                if utm_parameters and not utm_parameters.startswith('?'):
                    utm_parameters = '?' + utm_parameters
                
                link = base_link + utm_parameters

                call_to_action_type = config.get('call_to_action', 'SHOP_NOW')

                object_story_spec = {
                    "page_id": config.get('facebook_page_id', ''),
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

                video_id, image_hash = upload_video(video_path, task_id, config)
                if not video_id:
                    print(f"Failed to upload video: {media_file}")
                    return
                
                if not image_hash:
                    print(f"Failed to upload thumbnail")
                    return

                base_link = config.get('link', '')
                utm_parameters = config.get('url_parameters', 'utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}')

                if utm_parameters and not utm_parameters.startswith('?'):
                    utm_parameters = '?' + utm_parameters

                link = base_link + utm_parameters

                call_to_action_type = config.get('call_to_action', 'SHOP_NOW')

                object_story_spec = {
                    "page_id": config.get('facebook_page_id', ''),
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
                if media_file.lower().endswith(('.mp4', '.mov', '.avi')):
                    # Video processing
                    video_path = media_file
                    video_id, image_hash = upload_video(video_path, task_id, config)

                    if not video_id:
                        print(f"Failed to upload video: {media_file}")
                        return
                    
                    if not image_hash:
                        print(f"Failed to upload thumbnail")
                        return


                    card = {
                        "link": config.get('link', ''),
                        "video_id": video_id,
                        "call_to_action": {
                            "type": config.get('call_to_action', 'SHOP_NOW'),  # Default to "SHOP_NOW" if not provided
                            "value": {
                                "link": config.get('link', '')
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
                        "link": config.get('link', ''),
                        "image_hash": image_hash,
                        "call_to_action": {
                            "type": config.get('call_to_action', 'SHOP_NOW'),  # Default to "SHOP_NOW" if not provided
                            "value": {
                                "link": config.get('link', '')
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