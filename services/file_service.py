import os
import shutil
import logging

# Supported file extensions for videos and images
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

def get_all_video_files(directory):
    """
    Recursively finds all video files in a given directory.

    Args:
        directory (str): The root directory to search.

    Returns:
        list: A list of absolute file paths for found video files.
    """
    if not os.path.exists(directory):
        logging.warning(f"Directory not found: {directory}")
        return []

    video_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(tuple(VIDEO_EXTENSIONS)):
                video_files.append(os.path.join(root, file))

    return video_files


def get_all_image_files(directory):
    """
    Recursively finds all image files in a given directory.

    Args:
        directory (str): The root directory to search.

    Returns:
        list: A list of absolute file paths for found image files.
    """
    if not os.path.exists(directory):
        logging.warning(f"Directory not found: {directory}")
        return []

    image_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(tuple(IMAGE_EXTENSIONS)):
                image_files.append(os.path.join(root, file))

    return image_files


def clean_temp_files(directory):
    """
    Deletes the specified directory and its contents.

    Args:
        directory (str): The directory to delete.

    Returns:
        bool: True if deletion is successful, False otherwise.
    """
    if not os.path.exists(directory):
        logging.warning(f"Attempted to delete non-existent directory: {directory}")
        return False

    try:
        shutil.rmtree(directory, ignore_errors=True)
        logging.info(f"Successfully deleted directory: {directory}")
        return True
    except Exception as e:
        logging.error(f"Error deleting directory {directory}: {e}")
        return False