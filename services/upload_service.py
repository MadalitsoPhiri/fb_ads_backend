import logging
import time
import os
import requests
import subprocess

# Facebook Ads SDK
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adimage import AdImage

# External libraries
from PIL import Image

#utils and services
from utils.error_handler import emit_error
from services.task_manager import check_cancellation


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

def upload_video(app, video_file, task_id, config):
    """Uploads a video, extracts its first frame as a thumbnail, and uploads the thumbnail."""

    with app.app_context():  
        try:
            check_cancellation(task_id)
            video = AdVideo(parent_id=config['ad_account_id'])
            video[AdVideo.Field.filepath] = video_file
            video.remote_create()
            video_id = video.get_id()

            if not video_id:
                print("Failed to upload video")
                return None, None

            print(f"⏳ Video {video_id} uploaded. Waiting for processing to complete...")

            # Polling for video processing completion
            success = poll_video_status(video_id, config['access_token'])

            # Extract and upload the thumbnail
            thumbnail_hash = None
            thumbnail_path = extract_thumbnail(video_file)
            if thumbnail_path:
                thumbnail_hash = upload_image(app, thumbnail_path, task_id, config)

            if success:
                print(f"✅ Video {video_id} is fully processed and ready to use.")
                return video_id, thumbnail_hash
            else:
                print(f"⚠️ Video {video_id} failed to process in time.")
                return None, None
        except Exception as e:
            emit_error(f"Error uploading video: {e}")
            return None, None
    
def upload_image(app, image_file, task_id, config):
    with app.app_context():  

        check_cancellation(task_id)
        
        # Convert WebP to JPEG if necessary
        if image_file.lower().endswith(".webp"):
            try:
                image_file = convert_webp_to_jpeg(image_file)
                logging.info(f"Converted WebP to JPEG: {image_file}")
            except Exception as e:
                emit_error(f"Error converting WebP to JPEG: {e}")
                return None

        try:
            image = AdImage(parent_id=config['ad_account_id'])
            image[AdImage.Field.filename] = image_file
            image.remote_create()

            # Correct way to get the hash value
            image_hash = image.get(AdImage.Field.hash)

            if not image_hash:
                logging.error("Error: Response does not contain image hash!")
                return None

            logging.info(f"Uploaded image with hash: {image_hash}")
            return image_hash

        except Exception as e:
            emit_error(f"Error uploading image: {e}")
            return None


