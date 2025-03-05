import logging
import time
import os
import shutil

# External libraries
from tqdm import tqdm

# Concurrency tools
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.get_socket import get_socketio

from task_manager import check_cancellation, TaskCanceledException, cleanup_task_pid
from file_service import has_subfolders, get_all_files, clean_temp_files

def process_media(task_id, campaign_id, folders, config, total_media, temp_dir):
    try:
        get_socketio.emit('progress', {'task_id': task_id, 'progress': 0, 'step': f"0/{total_media}"})
        processed_media = 0

        with tqdm(total=total_media, desc="Processing media") as pbar:
            last_update_time = time.time()
            for folder in folders:
                check_cancellation(task_id)
                folder_path = os.path.join(temp_dir, folder)

                if has_subfolders(folder_path):
                    for subfolder in os.listdir(folder_path):
                        subfolder_path = os.path.join(folder_path, subfolder)
                        if os.path.isdir(subfolder_path):
                            media = get_all_files(subfolder_path)
                            if not media:
                                continue

                            ad_set = create_ad_set(campaign_id, subfolder, config, task_id)
                            if not ad_set:
                                continue

                            if config["ad_format"] == 'Single image or video':
                                with ThreadPoolExecutor(max_workers=10) as executor:
                                    future_to_file = {executor.submit(create_ad, ad_set.get_id(), file, config, task_id): file for file in media}

                                    for future in as_completed(future_to_file):
                                        check_cancellation(task_id)
                                        file = future_to_file[future]
                                        try:
                                            future.result()
                                        except TaskCanceledException:
                                            logging.warning(f"Task {task_id} has been canceled during processing video {file}.")
                                            return
                                        except Exception as e:
                                            logging.error(f"Error processing video {file}: {e}")
                                            get_socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                        finally:
                                            processed_media += 1
                                            pbar.update(1)

                                            current_time = time.time()
                                            if current_time - last_update_time >= 1:
                                                get_socketio.emit('progress', {'task_id': task_id, 'progress': processed_media / total_media * 100, 'step': f"{processed_media}/{total_media}"})
                                                last_update_time = current_time

                            elif config["ad_format"] == 'Carousel':
                                create_carousel_ad(ad_set.get_id(), media, config, task_id)

                else:
                    media = get_all_files(folder_path)
                    if not media:
                        continue

                    ad_set = create_ad_set(campaign_id, folder, config, task_id)
                    if not ad_set:
                        continue

                    if config["ad_format"] == 'Single image or video':
                        with ThreadPoolExecutor(max_workers=10) as executor:
                            future_to_file = {executor.submit(create_ad, ad_set.get_id(), file, config, task_id): file for file in media}

                            for future in as_completed(future_to_file):
                                check_cancellation(task_id)
                                file = future_to_file[future]
                                try:
                                    future.result()
                                except TaskCanceledException:
                                    logging.warning(f"Task {task_id} has been canceled during processing video {file}.")
                                    return
                                except Exception as e:
                                    logging.error(f"Error processing video {file}: {e}")
                                    get_socketio.emit('error', {'task_id': task_id, 'message': str(e)})
                                finally:
                                    processed_media += 1
                                    pbar.update(1)

                                    current_time = time.time()
                                    if current_time - last_update_time >= 0.5:
                                        get_socketio.emit('progress', {'task_id': task_id, 'progress': processed_media / total_media * 100, 'step': f"{processed_media}/{total_media}"})
                                        last_update_time = current_time

                    elif config["ad_format"] == 'Carousel':
                        create_carousel_ad(ad_set.get_id(), media, config, task_id)

        get_socketio.emit('progress', {'task_id': task_id, 'progress': 100, 'step': f"{total_media}/{total_media}"})
        get_socketio.emit('task_complete', {'task_id': task_id})
    except TaskCanceledException:
        logging.warning(f"Task {task_id} has been canceled during video processing.")
    except Exception as e:
        logging.error(f"Error in processing videos: {e}")
        get_socketio.emit('error', {'task_id': task_id, 'message': str(e)})
    finally:
        cleanup_task_pid(task_id)
        clean_temp_files(temp_dir, ignore_errors=True)