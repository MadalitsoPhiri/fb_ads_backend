from flask import Blueprint

# Define the blueprints
campaigns = Blueprint('campaigns', __name__)
task_bp = Blueprint("task_bp", __name__)

# Import all routes from the respective route files
from .campaign_routes import *
from .task_routes import *