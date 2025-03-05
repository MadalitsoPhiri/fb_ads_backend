from flask import Blueprint

# Define the blueprints
file_upload = Blueprint('file_upload', __name__)
ads = Blueprint('ads', __name__)
campaigns = Blueprint('campaigns', __name__)

# Import all routes from the respective route files
from .file_upload import *
from .ad_routes import *
from .campaign_routes import *