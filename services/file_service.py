import logging
import shutil
from pathlib import Path

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
    return [str(file) for file in directory.rglob("*") if file.suffix.lower() in extensions]

def get_all_video_files(directory):
    """Wrapper for retrieving video files."""
    return get_files(directory, VIDEO_EXTENSIONS)

def get_all_image_files(directory):
    """Wrapper for retrieving image files."""
    return get_files(directory, IMAGE_EXTENSIONS)

def get_all_files(directory):
    """Wrapper for retrieving both image and video files."""
    return get_files(directory, MEDIA_EXTENSIONS)


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
    Saves uploaded files to a specified directory while skipping hidden files.

    Args:
        upload_folder (list): List of uploaded file objects.
        destination (str or Path): The destination directory.

    Returns:
        None
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    for file in upload_folder:
        if not file.filename.startswith('.') or file.filename.lower() in ['.ds_store', 'thumbs.db']:  # Skip hidden files
            file_path = destination / file.filename
            file.save(str(file_path))

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
