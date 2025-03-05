import logging
import shutil
from pathlib import Path
import glob
import os

# Supported file extensions
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS.union(IMAGE_EXTENSIONS)

def get_files(directory, extensions):
    """Recursively finds all files with the given extensions in a directory."""
    directory = Path(directory)
    if not directory.exists():
        logging.warning(f"Directory not found: {directory}")
        return []
    
    files = [
        str(file) for file in directory.rglob("*")
        if file.suffix.lower() in extensions and Path(file).name.lower() != ".ds_store"
    ]
    logging.info(f"Found {len(files)} files in {directory}")
    return files

def get_all_video_files(directory):
    """Wrapper for retrieving video files."""
    return get_files(directory, VIDEO_EXTENSIONS)

def get_all_image_files(directory):
    """Wrapper for retrieving image files."""
    return get_files(directory, IMAGE_EXTENSIONS)

def get_all_files(directory):
    """Wrapper for retrieving both image and video files."""
    return get_files(directory, MEDIA_EXTENSIONS)

def get_total_media_count(directory):
    """
    Returns the total number of media files (images & videos) in a directory,
    including files in subdirectories.

    Args:
        directory (str or Path): The root directory to scan.

    Returns:
        int: Total count of media files found.
    """
    directory = Path(directory)
    if not directory.exists():
        logging.warning(f"Directory does not exist: {directory}")
        return 0

    media_files = get_files(directory, MEDIA_EXTENSIONS)
    logging.info(f"Total media files found in {directory}: {len(media_files)}")

    return len(media_files)

def clean_temp_files(directory):
    """Deletes the specified directory and its contents."""
    directory = Path(directory)
    if not directory.exists():
        logging.warning(f"Attempted to delete non-existent directory: {directory}")
        return False

    try:
        shutil.rmtree(directory)
        logging.info(f"Successfully deleted directory: {directory}")
        return True
    except Exception as e:
        logging.error(f"Error deleting directory {directory}: {e}")
        return False

def save_uploaded_files(upload_folder, destination):
    """
    Saves uploaded files to a specified directory while preserving folder structure.

    Args:
        upload_folder (list): List of uploaded file objects.
        destination (str or Path): The destination directory.

    Returns:
        None
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    logging.info(f"Saving uploaded files to: {destination}")

    for file in upload_folder:
        file_name = Path(file.filename).name
        if file_name.startswith('.') or file_name.lower() in [".ds_store", "thumbs.db"]:
            continue  # Skip hidden files
        parent_folder = Path(file.filename).parent  # Get subfolder name if any
        
        # Create a subdirectory for the file
        file_dest_dir = destination / parent_folder
        file_dest_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dest_dir / file_name
        file.save(str(file_path))

        logging.info(f"File saved: {file_path}")

def get_subfolders(directory):
    """
    Retrieves a list of subfolders within a given directory.

    Args:
        directory (str or Path): The directory to check.

    Returns:
        list: List of subfolder names.
    """
    directory = Path(directory)
    return [str(f) for f in directory.iterdir() if f.is_dir()]

def has_subfolders(directory):
    """
    Checks if a given directory has any subfolders.

    Args:
        directory (str or Path): The directory to check.

    Returns:
        bool: True if subfolders exist, False otherwise.
    """
    directory = Path(directory)
    return any(f.is_dir() for f in directory.iterdir())
