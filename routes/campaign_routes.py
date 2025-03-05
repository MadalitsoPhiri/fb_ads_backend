import logging
import tempfile
from pathlib import Path

# Flask-related imports
from flask import Blueprint, request, jsonify


# Services
from services import is_campaign_budget_optimized
from services.task_manager import add_task
from services.campaign_service import (
    process_campaign_config, 
    find_campaign_by_id, 
    get_campaign_budget_optimization, 
    create_campaign
)

# Utilities
from utils.validators import validate_campaign_request
from utils.error_handler import emit_error
from utils.get_socket import get_socketio
from services.file_service import (
    save_uploaded_files,
    get_subfolders,
    get_all_files
)

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

        # Check if a campaign ID is provided, otherwise create a new campaign
        if config.get("campaign_id"):
            campaign_id = find_campaign_by_id(config)
            if not campaign_id:
                logging.error(f"Campaign ID {config['campaign_id']} not found for ad account {config['ad_account_id']}")
                return jsonify({"error": "Campaign ID not found"}), 404

            existing_campaign_budget_optimization = get_campaign_budget_optimization(config)
            config['is_existing_cbo'] = existing_campaign_budget_optimization.get('is_campaign_budget_optimization', False)
        else:
            # Create a new campaign using structured data
            campaign_id, campaign = create_campaign(config)
            if not campaign_id:
                logging.error(f"Failed to create campaign with name {config['campaign_name']}")
                return jsonify({"error": "Failed to create campaign"}), 500

        temp_dir = Path(tempfile.mkdtemp())

        # Save uploaded files
        save_uploaded_files(config["upload_folder"], temp_dir)

        # Get all subfolders
        folders = get_subfolders(temp_dir)

        total_media = sum(len(get_all_files(folder)) for folder in folders)
        
        # Call the appropriate processing function based on media types
        get_socketio.start_background_task(target=process_media, task_id=config["task_id"], campaign_id=campaign_id, folders=folders, config=config, total_media=total_media)

        return jsonify({"message": "Campaign processing started", "task_id": config["task_id"]})

    except Exception as e:
        logging.error(f"Error in handle_create_campaign: {e}")
        return jsonify({"error": "Internal server error"}), 500
