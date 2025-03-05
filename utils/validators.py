import json
import logging
from flask import request, jsonify

def validate_campaign_request():
    """
    Validates required fields for creating a campaign.

    Returns:
        tuple: (bool, response, status_code) - True if valid, False if error with response message.
    """
    required_fields = ["campaign_name", "ad_account_id", "task_id"]
    missing_fields = [field for field in required_fields if request.form.get(field) is None]

    if missing_fields:
        return False, jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    return True, None, None


def validate_json_payload():
    """
    Validates and extracts the 'platforms' and 'placements' JSON fields from request.

    Returns:
        tuple: (dict, dict, response) - Validated platforms and placements dictionaries.
               Returns (None, None, JSON response) if validation fails.
    """
    try:
        # Retrieve JSON or form values
        platforms = request.json.get("platforms", "{}") if request.is_json else request.form.get("platforms", "{}")
        placements = request.json.get("placements", "{}") if request.is_json else request.form.get("placements", "{}")

        # Convert to dict if necessary
        if not isinstance(platforms, dict):
            platforms = json.loads(platforms)
        if not isinstance(placements, dict):
            placements = json.loads(placements)

        # Ensure they are still dictionaries after parsing
        if not isinstance(platforms, dict) or not isinstance(placements, dict):
            raise ValueError("Invalid JSON structure")

        logging.info(f"Platforms after processing: {platforms}")
        logging.info(f"Placements after processing: {placements}")

        return platforms, placements, None

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logging.error(f"Error decoding platforms or placements JSON: {e}")
        return None, None, jsonify({"error": "Invalid platforms or placements JSON"}), 400
