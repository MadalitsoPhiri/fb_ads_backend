# !IMPORTANT : Ensure this is the first import
# Patch eventlet to support asynchronous operations (needed for WebSocket support)
import eventlet
eventlet.monkey_patch()

# Flask-related imports
from flask import Flask 
from flask_cors import CORS  # Handles Cross-Origin Resource Sharing (CORS) for security
from flask_socketio import SocketIO  # Enables real-time WebSocket communication

# Importing API route blueprints
from routes import campaigns, task_bp

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for handling cross-origin requests
CORS(app)

# Initialize SocketIO for WebSocket support
socketio = SocketIO(app, cors_allowed_origins="*")

# Store SocketIO instance in Flask extensions for easy access in other modules
app.extensions['socketio'] = socketio

# Register API routes with URL prefixes
app.register_blueprint(campaigns, url_prefix='/campaigns')  # Routes related to campaign management
app.register_blueprint(task_bp, url_prefix='/task_bp')  # Routes related to task handling

# Run the Flask application with WebSocket support
if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5001)
