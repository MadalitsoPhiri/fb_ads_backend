import logging
import json
from datetime import datetime, timedelta

from utils.error_handler import emit_error  
from services.task_manager import check_cancellation
from utils.facebook_client import FacebookAdsClient
from utils.json_parser import parse_custom_audiences
from utils.validators import validate_json_payload

# Facebook Ads SDK
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign

def create_campaign(data):
    """
    Creates a Facebook Ads campaign.

    Args:
        data (dict): Campaign details, including:
            - task_id (str): The unique task ID for cancellation tracking.
            - app_id (str): Facebook App ID.
            - app_secret (str): Facebook App Secret.
            - access_token (str): Facebook Access Token.
            - name (str): Name of the campaign.
            - objective (str): Campaign objective.
            - buying_type (str): Buying type (e.g., AUCTION).
            - budget_value (float): Campaign budget value.
            - budget_optimization (str): Either 'DAILY_BUDGET' or 'LIFETIME_BUDGET'.
            - is_cbo (bool): Whether Campaign Budget Optimization (CBO) is enabled.
            - bid_strategy (str): The bid strategy.
            - ad_account_id (str): The Ad Account ID.

    Returns:
        tuple: Campaign ID and campaign object if successful, else (None, None).
    """
    check_cancellation(data["task_id"])  # Ensure the task isn't canceled

    try:
        client = FacebookAdsClient(data["app_id"], data["app_secret"], data["access_token"])

        # Define basic campaign parameters
        campaign_params = {
            "name": data["name"],
            "objective": data["objective"],
            "special_ad_categories": ["NONE"],  # Modify this based on actual categories if necessary
            "buying_type": data["buying_type"],
        }

        # Handle budget allocation if the campaign is set to 'AUCTION'
        if data["buying_type"] == "AUCTION":
            budget_value_cents = int(float(data["budget_value"]) * 100)  # Convert to cents
            if data["is_cbo"]:
                campaign_params["daily_budget"] = budget_value_cents if data["budget_optimization"] == "DAILY_BUDGET" else None
                campaign_params["lifetime_budget"] = budget_value_cents if data["budget_optimization"] == "LIFETIME_BUDGET" else None
                campaign_params["bid_strategy"] = data.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")

        # Create the campaign in the Facebook Ads API
        campaign = AdAccount(data["ad_account_id"]).create_campaign(fields=[AdAccount.Field.id], params=campaign_params)
        logging.info(f"Successfully created campaign with ID: {campaign['id']}")
        return campaign["id"], campaign

    except Exception as e:
        error_msg = f"Error creating campaign: {e}"
        logging.error(error_msg)
        emit_error(data["task_id"], error_msg)  # Notify frontend of failure
        return None, None
    

def get_campaign_budget_optimization(data):
    """
    Retrieves budget optimization details for a specific campaign.

    Args:
        data (dict): Campaign details, including:
            - campaign_id (str): The ID of the campaign.

    Returns:
        dict: Campaign details including name, status, budget, and objective.
        None: If an error occurs.
    """
    try:
        campaign = Campaign(data["campaign_id"]).api_get(fields=[
            Campaign.Field.name,
            Campaign.Field.effective_status,
            Campaign.Field.daily_budget,
            Campaign.Field.lifetime_budget,
            Campaign.Field.objective
        ])

        # Check if the campaign has budget optimization enabled
        is_cbo = campaign.get('daily_budget') is not None or campaign.get('lifetime_budget') is not None

        return {
            "name": campaign.get("name"),
            "effective_status": campaign.get("effective_status"),
            "daily_budget": campaign.get("daily_budget"),
            "lifetime_budget": campaign.get("lifetime_budget"),
            "is_campaign_budget_optimization": is_cbo,
            "objective": campaign.get("objective", "OUTCOME_TRAFFIC"),  # Default to 'OUTCOME_TRAFFIC' if not found
        }
    
    except Exception as e:
        logging.error(f"Error fetching campaign details for {data['campaign_id']}: {e}")
        return None


def is_campaign_budget_optimized(campaign_id, ad_account_id):
    """
    Checks if a given campaign has budget optimization enabled.

    Args:
        campaign_id (str): The ID of the campaign.
        ad_account_id (str): The Ad Account ID.

    Returns:
        bool: True if budget optimization is enabled, False otherwise.
    """
    budget_optimization_info = get_campaign_budget_optimization({"campaign_id": campaign_id})
    return budget_optimization_info.get("is_campaign_budget_optimization", False) if budget_optimization_info else False


def find_campaign_by_id(campaign_id, ad_account_id):
    """
    Finds and returns a campaign ID if it exists.

    Args:
        campaign_id (str): The ID of the campaign.
        ad_account_id (str): The Ad Account ID.

    Returns:
        str: Campaign ID if found.
        None: If the campaign is not found or an error occurs.
    """
    try:
        campaigns = AdAccount(ad_account_id).get_campaigns(
            fields=['name'],
            params={'filtering': [{'field': 'id', 'operator': 'EQUAL', 'value': campaign_id}]}
        )

        if campaigns:
            return campaign_id
        return None  # Campaign not found

    except Exception as e:
        logging.error(f"Error finding campaign by ID {campaign_id}: {e}")
        return None

def get_ad_account_timezone(ad_account_id, app_id, app_secret, access_token):
    """
    Fetches the timezone of a given Facebook Ad Account.

    Args:
        ad_account_id (str): The Facebook Ad Account ID (e.g., "act_123456789").
        app_id (str): Facebook App ID for authentication.
        app_secret (str): Facebook App Secret.
        access_token (str): Facebook Access Token.

    Returns:
        str: Timezone name (e.g., "America/Los_Angeles") if successful, else None.
    """
    try:
        # Initialize the Facebook Ads API before making any API calls
        client = FacebookAdsClient(app_id, app_secret, access_token)

        # Retrieve the ad account details
        ad_account = AdAccount(ad_account_id).api_get(fields=[AdAccount.Field.timezone_name])

        timezone_name = ad_account.get('timezone_name')
        logging.info(f"Fetched timezone for Ad Account {ad_account_id}: {timezone_name}")

        return timezone_name

    except Exception as e:
        logging.error(f"Error fetching timezone for Ad Account {ad_account_id}: {e}")
        return None  # Return None in case of failure
    
def process_campaign_config(request):
    """
    Extracts and processes campaign configuration from request.

    Args:
        request (flask.Request): The request object containing campaign details.

    Returns:
        dict: Processed campaign configuration.
    """
    try:
        # Parse JSON fields
        flexible_spec = json.loads(request.form.get("interests", "[]"))
        custom_audiences = parse_custom_audiences(request.form.get("custom_audiences", "[]"))

        # Receive the JavaScript objects directly
        if request.is_json:
            platforms = request.json.get('platforms', '{}')
        else:
            platforms = request.form.get('platforms', '{}')
        
        if request.is_json:
            placements = request.json.get('placements', '{}')
        else:
            placements = request.form.get('placements', '{}')
        
        # Validate platforms and placements JSON
        platforms, placements, error_response = validate_json_payload()
        if error_response:
            return error_response
        
        ad_account_id = request.form.get("ad_account_id")
        app_id = request.form.get("app_id")
        app_secret = request.form.get("app_secret")
        access_token = request.form.get("access_token")
        ad_account_timezone = get_ad_account_timezone(ad_account_id, app_id, app_secret, access_token)

        # Extract fields with defaults
        config = {
            "upload_folder": request.files.getlist('uploadFolders'),
            "campaign_name": request.form.get("campaign_name", ""),
            "campaign_id": request.form.get("campaign_id", ""),
            "task_id": request.form.get("task_id", ""),
            "ad_account_id": ad_account_id,
            "pixel_id": request.form.get("pixel_id", ""),
            "facebook_page_id": request.form.get("facebook_page_id", ""),
            "app_id": app_id,
            "app_secret": app_secret,
            "access_token": access_token,
            "headline": request.form.get("headline", ""),
            "link": request.form.get("destination_url", ""),
            'utm_parameters': request.form.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}'),
            "ad_format": request.form.get("ad_format", "Single image or video"),
            "objective": request.form.get("objective", "OUTCOME_SALES"),
            "campaign_budget_optimization": request.form.get('campaign_budget_optimization', 'AD_SET_BUDGET_OPTIMIZATION'),
            "budget_value": request.form.get("campaign_budget_value", ""),
            "buying_type": request.form.get("buying_type", "AUCTION"),
            'bid_strategy': request.form.get('campaign_bid_strategy', 'LOWEST_COST_WITHOUT_CAP'),
            "object_store_url": request.form.get("object_store_url", ""),
            "bid_amount": request.form.get("bid_amount", "0.0"),
            "is_cbo": request.form.get("isCBO", "false").lower() == "true",
            "custom_audiences": custom_audiences,
            "flexible_spec": flexible_spec,
            "geo_locations": request.form.get("location", ""),
            "age_range": request.form.get("age_range", ""),
            'age_range_max': request.form.get('age_range_max', '65'),
            'optimization_goal': request.form.get('performance_goal', 'OFFSITE_CONVERSIONS'),
            'event_type': request.form.get('event_type', 'PURCHASE'),
            'attribution_setting': request.form.get('attribution_setting', '7d_click'),
            'instagram_actor_id': request.form.get('instagram_account', ''),
            'ad_creative_primary_text': request.form.get('ad_creative_primary_text', ''),
            "ad_creative_headline": request.form.get("ad_creative_headline", ""),
            "ad_creative_description": request.form.get("ad_creative_description", ""),
            'call_to_action': request.form.get('call_to_action', 'SHOP_NOW'),
            "destination_url": request.form.get("destination_url", ""),
            "app_events": request.form.get(
                "app_events",
                (datetime.now() + timedelta(days=1))
                .replace(hour=4, minute=0, second=0, microsecond=0)
                .strftime("%Y-%m-%dT%H:%M:%S"),
            ),  # Default to tomorrow at 4 AM if not provided
            'language_customizations': request.form.get('language_customizations', 'en'),
            'url_parameters': request.form.get('url_parameters', '?utm_source=Facebook&utm_medium={{adset.name}}&utm_campaign={{campaign.name}}&utm_content={{ad.name}}'),
            'gender': request.form.get('gender', 'All'),
            'ad_set_budget_optimization': request.form.get('ad_set_budget_optimization', 'DAILY_BUDGET'),
            "ad_set_budget_value": request.form.get("ad_set_budget_value", ""),
            'ad_set_bid_strategy': request.form.get('ad_set_bid_strategy', 'LOWEST_COST_WITHOUT_CAP'),
            "ad_set_end_time": request.form.get("ad_set_end_time", ""),
            "platforms": platforms,
            "placements": placements,
            "ad_account_timezone": ad_account_timezone,
        }

        logging.info(f"Processed campaign config: {config}")
        return config

    except Exception as e:
        logging.error(f"Error processing campaign config: {e}")
        return None