from flask import Blueprint

# Define the blueprints
file_upload = Blueprint('file_upload', __name__)

# Import all routes from the respective route files
from .file_upload import *
