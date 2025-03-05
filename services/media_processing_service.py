import logging
import time
import os

# External Libraries
from tqdm import tqdm

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed

# Utilities & Services
from utils.get_socket import get_socketio
from services.task_manager import check_cancellation, TaskCanceledException, cleanup_task_pid
from services.file_service import has_subfolders, get_all_files, clean_temp_files
from services.adset_services import create_ad_set
from services.ad_service import create_ad, create_carousel_ad

def process_media(app, task_id, campaign_id, folders, config, total_media, temp_dir):
    """
    Processes media files for an ad campaign by creating appropriate ad sets and ads.

    Args:
        task_id (str): Unique identifier for the task.
        campaign_id (str): The campaign ID associated with the media.
        folders (list): List of folders containing media.
        config (dict): Configuration details for the campaign.
        total_media (int): Total number of media files to process.
        temp_dir (str): Path to the temporary directory storing uploaded files.
    """
    # Initialize progress tracking safely
    if total_media == 0:
        get_socketio().emit('progress', {'task_id': task_id, 'progress': 100, 'step': "No media found"})
        get_socketio().emit('task_complete', {'task_id': task_id})
        return

    # Manually push the app context inside the background thread
    with app.app_context():  
        try:
            # Initialize progress tracking
            get_socketio().emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_media}"})

            # Display progress bar for CLI/debugging purposes
            with tqdm(total=total_media, desc="Processing media") as pbar:

                # Iterate through each media folder
                for folder in folders:
                    check_cancellation(task_id)  # Check if task was canceled
                    folder_path = os.path.join(temp_dir, folder)

                    # If the folder contains subfolders, process them separately
                    if has_subfolders(folder_path):
                        for subfolder in os.listdir(folder_path):
                            subfolder_path = os.path.join(folder_path, subfolder)
                            if os.path.isdir(subfolder_path):
                                media = get_all_files(subfolder_path)
                                if not media:
                                    continue

                                # Create an ad set for the subfolder
                                ad_set_name = os.path.basename(subfolder)
                                ad_set = create_ad_set(campaign_id, ad_set_name, config, task_id)

                                if not ad_set:
                                    continue

                                # Process ads based on format
                                if config["ad_format"] == 'Single image or video':
                                    _process_single_ads(app, task_id, ad_set.get_id(), media, config, pbar, total_media)
                                elif config["ad_format"] == 'Carousel':
                                    create_carousel_ad(app, ad_set.get_id(), media, config, task_id)

                    # If no subfolders, process the folder directly
                    else:
                        media = get_all_files(folder_path)
                        if not media:
                            continue

                        # Create an ad set for the folder
                        ad_set_name = os.path.basename(folder)
                        ad_set = create_ad_set(campaign_id, ad_set_name, config, task_id)
                        if not ad_set:
                            continue

                        # Process ads based on format
                        if config["ad_format"] == 'Single image or video':
                            _process_single_ads(app, task_id, ad_set.get_id(), media, config, pbar, total_media)
                        elif config["ad_format"] == 'Carousel':
                            create_carousel_ad(app, ad_set.get_id(), media, config, task_id)

            # Task complete: Notify via socket
            get_socketio().emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_media}/{total_media}"})
            get_socketio().emit('task_complete', {'task_id': task_id})

        except TaskCanceledException:
            logging.warning(f"Task {task_id} has been canceled during media processing.")
        except Exception as e:
            logging.error(f"Error in processing media: {e}")
            get_socketio().emit('error', {'task_id': task_id, 'message': str(e)})
        finally:
            # Clean up process PIDs and temporary files
            cleanup_task_pid(task_id)
            clean_temp_files(temp_dir)
        

def _process_single_ads(app, task_id, ad_set_id, media_files, config, pbar, total_media):
    """
    Processes media files as single ads using multithreading.

    Args:
        task_id (str): Unique task identifier.
        ad_set_id (str): The ad set ID for the campaign.
        media_files (list): List of media files to process.
        config (dict): Campaign configuration.
        pbar (tqdm): Progress bar object.
        total_media (int): Total number of media files.
    """
    with app.app_context():
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_file = {executor.submit(create_ad, app, ad_set_id, file, config, task_id): file for file in media_files}

            for future in as_completed(future_to_file):
                check_cancellation(task_id)  # Check if task was canceled
                file = future_to_file[future]
                try:
                    future.result()  # Process file
                except TaskCanceledException:
                    logging.warning(f"Task {task_id} has been canceled during processing media {file}.")
                    return
                except Exception as e:
                    logging.error(f"Error processing media {file}: {e}")
                    get_socketio().emit('error', {'task_id': task_id, 'message': str(e)})
                finally:
                    pbar.update(1)

                    # Emit progress update every 0.5 seconds
                    current_time = time.time()
                    if current_time - pbar.last_print_t >= 0.5:
                        get_socketio().emit('progress', {'task_id': task_id, 'progress': pbar.n / total_media * 100, 'step': f"{pbar.n}/{total_media}"})
                        pbar.last_print_t = current_time
