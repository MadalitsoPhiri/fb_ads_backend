# !IMPORTANT : Ensure this is the first import
# Patch eventlet to support asynchronous operations (needed for WebSocket support)
import eventlet
eventlet.monkey_patch()

import logging
import sys

# Flask-related imports
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

# Importing API route blueprints
from routes.campaign_routes import campaign_bp
from routes.task_routes import task_bp

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for handling cross-origin requests
CORS(app)

# Initialize SocketIO for WebSocket support
socketio = SocketIO(app, cors_allowed_origins="*")

# Store SocketIO instance in Flask extensions for easy access in other modules
app.extensions['socketio'] = socketio

# Register API routes with URL prefixes
app.register_blueprint(campaign_bp, url_prefix='/campaigns')  # Routes related to campaign management
app.register_blueprint(task_bp, url_prefix='/tasks')  # Routes related to task handling

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Ensure DEBUG messages are shown
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)  # Ensure logs are printed to the terminal
    ]
)

# Ensure Flask doesn't override logging
app.logger.setLevel(logging.DEBUG)

# Manually attach handlers to Flask logging
if not app.logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    app.logger.addHandler(handler)

# Run the Flask application with WebSocket support
if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5001)
