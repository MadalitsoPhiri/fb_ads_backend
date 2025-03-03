# Patch eventlet to support asynchronous operations
import eventlet
eventlet.monkey_patch()

import logging
import time
import json
import os
import tempfile
import re
import requests

# Flask-related imports
from flask import Flask, Blueprint, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Facebook Ads SDK
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage

# External libraries
from tqdm import tqdm
from PIL import Image

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed


file_upload = Blueprint("file_upload", __name__)

def emit_error(task_id, message):
    logging.error(f"Raw error message: {message}")  # Log the full raw message for debugging purposes

    # Initialize default title and message
    title = "Error"
    msg = "An unknown error occurred."

    # Step 1: Extract the JSON part from the raw error message using regex
    json_match = re.search(r'Response:\s*(\{.*\})', message, re.DOTALL)
    
    if json_match:
        # Step 2: Parse the extracted JSON part
        try:
            error_data = json.loads(json_match.group(1))

            # Step 3: Extract title and message from the parsed JSON
            title = error_data.get("error", {}).get("error_user_title", "Error")
            print("Title\n")
            print(title)
            msg = error_data.get("error", {}).get("error_user_msg", "An unknown error occurred.")
        except json.JSONDecodeError:
            logging.error("Failed to parse the error JSON from the response.")
    else:
        # If JSON is not found, just use the raw message as the fallback
        msg = message

    # Step 4: Emit the error title and message to the frontend
    socketio.emit('error', {
        'task_id': task_id,
        'title': title,
        'message': msg
    })

    # Emit only the title and message to the frontend
    socketio.emit('error', {'task_id': task_id, 'title': title, 'message': msg})

def convert_webp_to_jpeg(webp_file):
    jpeg_file = os.path.splitext(webp_file)[0] + ".jpg"
    with Image.open(webp_file) as img:
        img.convert("RGB").save(jpeg_file, "JPEG")
    return jpeg_file

# **Enhanced Polling Function**
def poll_video_status(video_id, access_token, timeout=600, poll_interval=5):
    """ Polls the Facebook API until the video is processed or timeout occurs. """
    session = requests.Session()
    status_url = f"https://graph-video.facebook.com/v19.0/{video_id}"
    params = {"fields": "status", "access_token": access_token}

    start_time = time.time()
    retry_attempts = 1  # For exponential backoff

    while time.time() - start_time < timeout:
        try:
            response = session.get(status_url, params=params).json()
            status = response.get("status", {}).get("video_status", "unknown")

            if status == "ready":
                print(f"✅ Video {video_id} is ready for use!")
                return True
            elif status in ["processing", "uploading"]:
                print(f"⏳ Video {video_id} still processing... Retrying in {poll_interval} seconds.")
            else:
                print(f"Unexpected video status: {status}")
                return False

        except Exception as e:
            logging.error(f"Error polling video status: {e}")

        time.sleep(poll_interval)
        poll_interval = min(30, poll_interval + 5)  # Exponential backoff (max 30s)
        retry_attempts += 1

    print(f"⚠️ Video {video_id} did not finish processing within {timeout} seconds.")
    return False

# **Final Upload Function**
def upload_video(video_file, task_id, config):
    """Uploads a video and polls the Facebook API until processing is complete."""
    try:
        video = AdVideo(parent_id=config['ad_account_id'])
        video[AdVideo.Field.filepath] = video_file
        video.remote_create()
        video_id = video.get_id()
        if not video_id:
            print("Failed to upload video")
            return None

        print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

        # Polling for video processing completion
        success = poll_video_status(video_id, config['access_token'])

        if success:
            print(f"✅ Video {video_id} is fully processed and ready to use.")
            return video_id
        else:
            print(f"⚠️ Video {video_id} failed to process in time.")
            return None
    except Exception as e:
        emit_error(task_id, f"Error uploading video: {e}")
        return None


def upload_image(image_file, task_id, config):
    try:
        image = AdImage(parent_id=config['ad_account_id'])
        image[AdImage.Field.filename] = image_file
        image.remote_create()
        logging.info(f"Uploaded image with hash: {image[AdImage.Field.hash]}")
        return image[AdImage.Field.hash]
    except Exception as e:
        error_msg = f"Error uploading image: {e}"
        emit_error(task_id, error_msg)
        return None

@file_upload.route("/upload_creatives", methods=["POST"])
def upload_creatives():
    """Receives videos & images and uploads them while preserving folder structure."""
    if 'uploadFolders' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    uploaded_structure = {}

    files = request.files.getlist('uploadFolders')
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_file = {}

        for file in files:
            folder_name = request.form.get(f"folder[{file.filename}]", "default")  # Extract folder from request
            if folder_name not in uploaded_structure:
                uploaded_structure[folder_name] = []

            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext in ['.webp']:
                # Convert WebP to JPEG before upload
                print(f"Converting {file.filename} from WebP to JPEG...")
                temp_dir = tempfile.gettempdir()
                webp_path = os.path.join(temp_dir, file.filename)
                file.save(webp_path)  # Save the uploaded webp file first
                
                jpeg_path = convert_webp_to_jpeg(webp_path)  # Convert to JPEG
                
                # Upload the converted JPEG instead
                with open(jpeg_path, "rb") as jpeg_file:
                    future_to_file[executor.submit(upload_image, jpeg_file, FACEBOOK_ACCESS_TOKEN, FACEBOOK_AD_ACCOUNT_ID)] = (folder_name, os.path.basename(jpeg_path), "image")
            
            elif file_ext in ['.jpg', '.jpeg', '.png']:
                # Upload images directly
                future_to_file[executor.submit(upload_image, file, FACEBOOK_ACCESS_TOKEN, FACEBOOK_AD_ACCOUNT_ID)] = (folder_name, file.filename, "image")
            
            elif file_ext in ['.mp4', '.mov', '.avi']:
                # Upload videos
                future_to_file[executor.submit(upload_video, file, FACEBOOK_ACCESS_TOKEN, FACEBOOK_AD_ACCOUNT_ID)] = (folder_name, file.filename, "video")

        for future in as_completed(future_to_file):
            folder_name, file_name, file_type = future_to_file[future]
            try:
                media_id, error = future.result()
                if media_id:
                    uploaded_structure[folder_name].append({
                        f"{file_type}_id": media_id, 
                        "file_name": file_name
                    })
                else:
                    return jsonify({"error": f"Failed to upload {file_name}: {error}"}), 500
            except Exception as e:
                return jsonify({"error": f"Exception during upload {file_name}: {str(e)}"}), 500

    return jsonify({"message": "Upload successful", "media_structure": uploaded_structure}), 200


@file_upload.route("/cancel_upload", methods=["POST"])
def cancel_upload():
    """Endpoint to cancel an ongoing upload."""
    pass
