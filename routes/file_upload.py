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
from flask import Flask, Blueprint, request, jsonify, current_app
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# Facebook Ads SDK
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage

# External libraries
from tqdm import tqdm
from PIL import Image
from io import BytesIO

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed


file_upload = Blueprint("file_upload", __name__)

def emit_error(message):
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

    socketio = current_app.extensions.get('socketio')
    if socketio:
        socketio.emit('error', {'title': title, 'message': msg})
    else:
        logging.error("SocketIO instance not found in current_app extensions")

    # Step 4: Emit the error title and message to the frontend
    socketio.emit('error', {
        'title': title,
        'message': msg
    })

    # Emit only the title and message to the frontend
    socketio.emit('error', {'title': title, 'message': msg})

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
def upload_video(video_file, ad_account_id, access_token):
    """Uploads a video and polls the Facebook API until processing is complete."""
    try:
        # Ensure the temp directory exists
        temp_dir = os.path.join(tempfile.gettempdir(), "uploaded_videos")
        os.makedirs(temp_dir, exist_ok=True) 

        temp_video_path = os.path.join(temp_dir, video_file.filename)
        video_file.save(temp_video_path)

        # ✅ Pass the actual file path to AdVideo
        video = AdVideo(parent_id=ad_account_id)
        video[AdVideo.Field.filepath] = temp_video_path
        video.remote_create()
        video_id = video.get_id()

        if not video_id:
            print("Failed to upload video")
            return None

        print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

        # Polling for video processing completion
        success = poll_video_status(video_id, access_token)

        if success:
            print(f"✅ Video {video_id} is fully processed and ready to use.")
            return video_id
        else:
            print(f"⚠️ Video {video_id} failed to process in time.")
            return None
    except Exception as e:
        emit_error(f"Error uploading video: {e}")
        return None
    finally:
        # Ensure temp file cleanup
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)


def upload_image(image_file, ad_account_id):
    """Uploads an image while ensuring a valid file path."""
    try:
        # Save the file temporarily
        temp_dir = os.path.join(tempfile.gettempdir(), "uploaded_images")
        os.makedirs(temp_dir, exist_ok=True)

        temp_image_path = os.path.join(temp_dir, image_file.filename)
        image_file.save(temp_image_path)

        # ✅ Pass the actual file path to AdImage
        image = AdImage(parent_id=ad_account_id)
        image[AdImage.Field.filename] = temp_image_path
        image.remote_create()
        
        logging.info(f"✅ Uploaded image with hash: {image[AdImage.Field.hash]}")
        return image[AdImage.Field.hash]
    
    except Exception as e:
        error_msg = f"❌ Error uploading image: {e}"
        emit_error(error_msg)
        return None
    finally:
        # Cleanup temp file
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)


@file_upload.route("/upload_creatives", methods=["POST"])
def upload_creatives():
    """Receives videos & images and uploads them while preserving folder structure."""

    app = current_app._get_current_object()

    # Get Facebook credentials from request
    access_token = request.form.get('access_token')
    ad_account_id = request.form.get('ad_account_id')
    app_id = request.form.get('app_id')
    app_secret = request.form.get('app_secret')

    if not access_token or not ad_account_id:
        return jsonify({"error": "Missing access token or ad account ID"}), 400
    
    FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v19.0')

    if 'uploadFolders' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    socketio = current_app.extensions.get('socketio')

    uploaded_structure = {}
    files = request.files.getlist('uploadFolders')
    total_files = len(files)  # Total files to be uploaded
    completed_files = 0  # Track completed uploads

    with app.app_context():
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_file = {}

            # Skip hidden/system files like .DS_Store
            for file in files:
                if file.filename.startswith('.') or file.filename.lower() in ['.ds_store', 'thumbs.db']:
                    continue  # Skip hidden/system files

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
                        future_to_file[executor.submit(upload_image, jpeg_file, ad_account_id)] = (folder_name, os.path.basename(jpeg_path), "image")
                
                elif file_ext in ['.jpg', '.jpeg', '.png']:
                    # Upload images directly
                    future_to_file[executor.submit(upload_image, file, ad_account_id)] = (folder_name, file.filename, "image")
                
                elif file_ext in ['.mp4', '.mov', '.avi']:
                    # Upload videos
                    future_to_file[executor.submit(upload_video, file, ad_account_id, access_token)] = (folder_name, file.filename, "video")

            for future in as_completed(future_to_file):
                folder_name, file_name, file_type = future_to_file[future]
                try:
                    media_id = future.result()
                    if media_id:
                        uploaded_structure[folder_name].append({
                            f"{file_type}_id": media_id, 
                            "file_name": file_name
                        })

                    # **Increment completed file count**
                    completed_files += 1
                    progress_percentage = (completed_files / total_files) * 100

                    # **Emit progress update**
                    socketio.emit('upload_progress', {
                        "completed_files": completed_files,
                        "total_files": total_files,
                        "progress": round(progress_percentage, 2),  
                        "status": "Uploading"
                    })

                except Exception as e:
                    logging.error(f"Exception during upload {file_name}: {str(e)}")

    # **Emit final success update**
    with app.app_context():
        socketio.emit('upload_progress', {
            "completed_files": completed_files,
            "total_files": total_files,
            "progress": 100,
            "status": "Completed"
        })
    
    return jsonify({"message": "Upload successful", "media_structure": uploaded_structure}), 200

@file_upload.route("/cancel_upload", methods=["POST"])
def cancel_upload():
    """ Endpoint to cancel an ongoing upload """
    return jsonify({"message": "Cancellation feature to be implemented"}), 200