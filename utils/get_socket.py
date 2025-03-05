import logging
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