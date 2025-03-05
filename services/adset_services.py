import json
from datetime import datetime, timedelta
from pytz import timezone

# Facebook Ads SDK
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet

# Task Management & Error Handling
from services.task_manager import check_cancellation
from utils.error_handler import emit_error

def convert_to_utc(local_time_str, ad_account_timezone):
    local_tz = timezone(ad_account_timezone)
    local_time = local_tz.localize(datetime.strptime(local_time_str, '%Y-%m-%dT%H:%M:%S'))
    utc_time = local_time.astimezone(timezone('UTC'))
    return utc_time.strftime('%Y-%m-%dT%H:%M:%S')

def create_ad_set(campaign_id, folder_name, config, task_id):
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