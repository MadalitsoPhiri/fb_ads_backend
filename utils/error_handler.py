import logging
import json
import re

# Flask-related imports
from flask import current_app

def get_socketio():
    """
    Retrieve the SocketIO instance dynamically from Flask's current_app extensions.
    
    This ensures that SocketIO is accessible from anywhere in the application
    without requiring direct access to the app instance.
    
    Raises:
        RuntimeError: If SocketIO has not been initialized in the Flask app.
    
    Returns:
        SocketIO: The initialized SocketIO instance.
    """
    socketio = current_app.extensions.get('socketio')
    if socketio is None:
        logging.error("SocketIO instance not found.")
        raise RuntimeError("SocketIO instance not initialized.")
    return socketio

def emit_error(message):
    """
    Emit an error message via SocketIO to notify the frontend of issues.

    This function extracts error details from the given message and formats them properly.
    If the message contains a JSON error response, it attempts to parse and extract
    user-friendly error titles and messages.

    Args:
        message (str): The raw error message to process and emit.

    Behavior:
        - Logs the full error message for debugging.
        - Attempts to extract error details from JSON if available.
        - Emits the formatted error message via SocketIO.
        - If parsing fails, defaults to the raw message.
    """
    logging.error(f"Raw error message: {message}")  # Log for debugging purposes

    # Default error title and message
    title, msg = "Error", "An unknown error occurred."

    # Attempt to extract JSON error response from the message using regex
    json_match = re.search(r'Response:\s*(\{.*\})', message, re.DOTALL)
    
    if json_match:
        try:
            # Parse the extracted JSON error response
            error_data = json.loads(json_match.group(1))

            # Extract error title and user-friendly message if available
            title = error_data.get("error", {}).get("error_user_title", "Error")
            msg = error_data.get("error", {}).get("error_user_msg", "An unknown error occurred.")
        except json.JSONDecodeError:
            logging.error("Failed to parse the error JSON from the response.")  # Log parsing failure
    
    else:
        # If no JSON structure is found, use the raw message as the error message
        msg = message

    try:
        # Emit the error message to the frontend using SocketIO
        get_socketio().emit('error', {'title': title, 'message': msg})
    except Exception as e:
        logging.error(f"Failed to emit error via socket: {e}")  # Log SocketIO emission failure
