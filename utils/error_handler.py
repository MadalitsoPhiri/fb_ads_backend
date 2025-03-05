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

def emit_error(title, message=None):
    """
    Emit an error message via SocketIO to notify the frontend of issues.
    
    Args:
        title (str): The error title.
        message (str, optional): Additional error message details.
    """
    logging.error(f"Raw error message: {title} {message or ''}")  # Log full error
    # If no message is provided, use title as message
    msg = message if message else title

    try:
        # Emit the error message to the frontend using SocketIO
        get_socketio().emit('error', {'title': title, 'message': msg})
    except Exception as e:
        logging.error(f"Failed to emit error via socket: {e}")  # Log failure
