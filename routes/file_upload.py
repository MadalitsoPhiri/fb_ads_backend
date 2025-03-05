import logging
import time
import json
import os
import tempfile
import re
import requests
import shutil
import subprocess

# Flask-related imports
from flask import Flask, Blueprint, request, jsonify, current_app
from flask_cors import CORS
from flask_socketio import emit

# Facebook Ads SDK
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage

# External libraries
from tqdm import tqdm
from PIL import Image
from threading import Lock
from uuid import uuid4

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed


file_upload = Blueprint("file_upload", __name__)

UPLOAD_TASKS = {}
UPLOAD_TASKS_LOCK = Lock() 

def get_socketio():
    """Retrieve the socketio instance dynamically."""
    from flask import current_app
    return current_app.extensions['socketio']

def emit_error(message):
    logging.error(f"Raw error message: {message}")  

    title, msg = "Error", "An unknown error occurred."

    json_match = re.search(r'Response:\s*(\{.*\})', message, re.DOTALL)
    
    if json_match:
        try:
            error_data = json.loads(json_match.group(1))
            title = error_data.get("error", {}).get("error_user_title", "Error")
            msg = error_data.get("error", {}).get("error_user_msg", "An unknown error occurred.")
        except json.JSONDecodeError:
            logging.error("Failed to parse the error JSON from the response.")
    else:
        msg = message

    get_socketio().emit('error', {'title': title, 'message': msg})

def extract_thumbnail(video_path):
    """Extracts the first frame (or closest keyframe) of a video using FFmpeg."""
    try:
        thumbnail_path = os.path.splitext(video_path)[0] + "_thumbnail.jpg"
        command = [
            'ffmpeg', '-i', video_path, 
            '-ss', '00:00:01.000', '-vframes', '1', 
            '-preset', 'ultrafast', '-threads', '4', 
            '-update', '1', thumbnail_path
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        if os.path.exists(thumbnail_path):
            return thumbnail_path
        else:
            logging.error("FFmpeg failed to generate a thumbnail.")
            return None

    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg error: {e}")
        return None

def convert_webp_to_jpeg(webp_file):
    jpeg_file = os.path.splitext(webp_file)[0] + ".jpg"
    with Image.open(webp_file) as img:
        img.convert("RGB").save(jpeg_file, "JPEG")
    return jpeg_file

def poll_video_status(video_id, access_token, timeout=600, poll_interval=5):
    session = requests.Session()
    status_url = f"https://graph-video.facebook.com/v19.0/{video_id}"
    params = {"fields": "status", "access_token": access_token}

    start_time = time.time()

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
        poll_interval = min(30, poll_interval + 5)  

    print(f"⚠️ Video {video_id} did not finish processing within {timeout} seconds.")
    return False

def upload_video(file_path, ad_account_id, access_token):
    """Uploads a video, extracts its first frame as a thumbnail, and uploads the thumbnail."""
    try:
        video = AdVideo(parent_id=ad_account_id)
        video[AdVideo.Field.filepath] = file_path
        video.remote_create()
        video_id = video.get_id()

        if not video_id:
            print("Failed to upload video")
            return None, None

        print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

        # Polling for video processing completion
        success = poll_video_status(video_id, access_token)

        # Extract and upload the thumbnail
        thumbnail_hash = None
        thumbnail_path = extract_thumbnail(file_path)
        if thumbnail_path:
            thumbnail_hash = upload_image(thumbnail_path, ad_account_id)

        if success:
            print(f"✅ Video {video_id} is fully processed and ready to use.")
            return video_id, thumbnail_hash
        else:
            print(f"⚠️ Video {video_id} failed to process in time.")
            return None, None
    except Exception as e:
        emit_error(f"Error uploading video: {e}")
        return None, None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def upload_image(file_path, ad_account_id):
    try:
        image = AdImage(parent_id=ad_account_id)
        image[AdImage.Field.filename] = file_path
        image.remote_create()
        
        logging.info(f"✅ Uploaded image with hash: {image[AdImage.Field.hash]}")
        return image[AdImage.Field.hash]
    
    except Exception as e:
        emit_error(f"Error uploading image: {e}")
        return None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@file_upload.route("/upload_creatives", methods=["POST"])
def upload_creatives():
    """Receives videos & images and uploads them while preserving folder structure."""

    access_token = request.form.get('access_token')
    ad_account_id = request.form.get('ad_account_id')
    app_id = request.form.get('app_id')
    app_secret = request.form.get('app_secret')
    task_id = request.form.get('task_id')  # ✅ Get task ID from frontend

    if not access_token or not ad_account_id:
        return jsonify({"error": "Missing access token or ad account ID"}), 400

    if not task_id:  # ✅ Ensure task_id exists
        return jsonify({"error": "Missing task ID"}), 400

    print(f"📌 Received Task ID from Frontend: {task_id}")

    FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v19.0')

    if 'uploadFolders' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    with UPLOAD_TASKS_LOCK:
        UPLOAD_TASKS[task_id] = []  # ✅ Store task ID immediately

    uploaded_structure = {}
    files = request.files.getlist('uploadFolders')
    total_files = len(files)
    completed_files = 0  

    temp_dir = os.path.join(tempfile.gettempdir(), f"uploaded_files_{task_id}")
    os.makedirs(temp_dir, exist_ok=True)

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_file = {}

        for file in files:
            actual_filename = os.path.basename(file.filename)
            if actual_filename.startswith('.') or file.filename.lower() in ['.ds_store', 'thumbs.db']:
                continue  

            folder_name = os.path.dirname(file.filename)  # Extract folder name correctly
            if not folder_name or folder_name == ".":
                folder_name = "default"  # Default folder if none detected
            folder_path = os.path.join(temp_dir, folder_name)
            os.makedirs(folder_path, exist_ok=True)

            file_path = os.path.join(folder_path, actual_filename)
            file.save(file_path)

            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext in ['.webp']:
                jpeg_path = convert_webp_to_jpeg(file_path)
                future = executor.submit(upload_image, jpeg_path, ad_account_id)
            elif file_ext in ['.jpg', '.jpeg', '.png']:
                future = executor.submit(upload_image, file_path, ad_account_id)
            elif file_ext in ['.mp4', '.mov', '.avi']:
                future = executor.submit(upload_video, file_path, ad_account_id, access_token)
            else:
                continue

            future_to_file[future] = (folder_name, file.filename)

            with UPLOAD_TASKS_LOCK:
                UPLOAD_TASKS[task_id].append(future)

        for future in as_completed(future_to_file):
            folder_name, file_name = future_to_file[future]
            
            try:
                result = future.result()

                if isinstance(result, tuple) and len(result) == 2:
                    media_id, thumbnail_hash = result
                else:
                    media_id = result
                    thumbnail_hash = ""

                if media_id:
                    uploaded_structure.setdefault(folder_name, []).append({
                        "media_id": media_id,
                        "file_name": f"Test/{folder_name}/{file_name}",  # Ensure correct folder path
                        "thumbnail_hash": thumbnail_hash
                    })

                completed_files += 1
                get_socketio().emit('upload_progress', {
                    "task_id": task_id,
                    "completed_files": completed_files,
                    "total_files": total_files,
                    "progress": round((completed_files / total_files) * 100, 2),
                    "status": "Uploading"
                })
            except Exception as e:
                logging.error(f"Exception during upload {file_name}: {str(e)}")


    get_socketio().emit('upload_progress', {
        "task_id": task_id,
        "completed_files": completed_files,
        "total_files": total_files,
        "progress": 100,
        "status": "Completed",
        "media_structure": uploaded_structure
    })

    with UPLOAD_TASKS_LOCK:
        if UPLOAD_TASKS.get(task_id) == "cancelled":
            print(f"🧹 Task {task_id} was cancelled. Cleaning up files...")
            shutil.rmtree(temp_dir, ignore_errors=True)
            del UPLOAD_TASKS[task_id]
        else:
            del UPLOAD_TASKS[task_id]  # Remove normal task tracking

    shutil.rmtree(temp_dir, ignore_errors=True)

    return jsonify({"message": "Upload successful", "task_id": task_id, "media_structure": uploaded_structure}), 200

@file_upload.route("/cancel_upload", methods=["POST"])
def cancel_upload():
    """Cancel an ongoing upload task."""
    
    print("Cancel upload request received...")

    data = request.get_json(silent=True) or request.form  
    task_id = data.get("task_id")

    if not task_id:
        return jsonify({"error": "Task ID required"}), 400

    print(f"Task ID to cancel: {task_id}")

    with UPLOAD_TASKS_LOCK:
        print(f"Active Upload Tasks: {list(UPLOAD_TASKS.keys())}")

        if task_id not in UPLOAD_TASKS:
            print(f"⚠️ Task ID {task_id} not found in active tasks!")
            return jsonify({"error": "No active upload task found"}), 404

        # Mark task as cancelled
        UPLOAD_TASKS[task_id] = "cancelled"

    get_socketio().emit('upload_progress', {
        "task_id": task_id,
        "progress": 0,
        "status": "Cancelled"
    })

    return jsonify({"message": f"Upload task {task_id} marked for cancellation."}), 200

