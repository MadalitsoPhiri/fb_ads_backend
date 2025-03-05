import logging

from utils.error_handler import emit_error  
from task_manager import check_cancellation
from utils.facebook_client import FacebookAdsClient


# Facebook Ads SDK
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign

# Function to create a campaign
def create_campaign(data):
    check_cancellation(data["task_id"])
    try:
        client = FacebookAdsClient(data["app_id"], data["app_secret"], data["access_token"])

        campaign_params = {
            "name": data["name"],
            "objective": data["objective"],
            "special_ad_categories": ["NONE"],
            "buying_type": data["buying_type"],
        }

        # Handling Auction Buying type
        if data["buying_type"] == "AUCTION":
            budget_value_cents = int(float(data["budget_value"]) * 100)  # Convert to cents
            if data["is_cbo"]:
                campaign_params["daily_budget"] = budget_value_cents if data["budget_optimization"] == "DAILY_BUDGET" else None
                campaign_params["lifetime_budget"] = budget_value_cents if data["budget_optimization"] == "LIFETIME_BUDGET" else None
                campaign_params["bid_strategy"] = data['bid_strategy']

        campaign = AdAccount(data["ad_account_id"]).create_campaign(fields=[AdAccount.Field.id], params=campaign_params)
        logging.info(f"Created campaign with ID: {campaign['id']}")
        return campaign['id'], campaign
    except Exception as e:
        error_msg = f"Error creating campaign: {e}"
        emit_error(data["task_id"], error_msg)
        return None, None
    
#function to check campaign budget optimization.
def get_campaign_budget_optimization(data):
    try:
        campaign = Campaign(data["campaign_id"]).api_get(fields=[
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