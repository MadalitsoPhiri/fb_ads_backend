import json
import logging

def parse_custom_audiences(audience_str):
    """
    Parses the JSON string containing custom audience data.

    Args:
        audience_str (str): JSON string of audiences.

    Returns:
        list: List of audience IDs.
    """
    try:
        audiences = json.loads(audience_str)
        return [{"id": audience["value"]} for audience in audiences]
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing custom audiences: {e}")
        return []
