import logging

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
from routes import file_upload


campaigns = Blueprint('campaigns', __name__)


@campaigns.route('/budget_optimization', methods=['POST'])
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
