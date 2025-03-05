from flask import current_app

def get_socketio():
    """Retrieve the socketio instance dynamically."""
    return current_app.extensions['socketio']
