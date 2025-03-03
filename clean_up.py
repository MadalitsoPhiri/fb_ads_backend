import os
import shutil
import time
import threading

class TempFileCleanup:
    """Handles automatic cleanup of temp files older than a specified time."""
    
    def __init__(self, temp_dir="temp_uploads", expiration_time=3600, cleanup_interval=600):
        """
        Initializes the cleanup process.

        :param temp_dir: Directory where temporary files are stored.
        :param expiration_time: Time in seconds after which a folder should be deleted.
        :param cleanup_interval: Time in seconds between cleanup checks.
        """
        self.temp_dir = temp_dir
        self.expiration_time = expiration_time
        self.cleanup_interval = cleanup_interval
        os.makedirs(self.temp_dir, exist_ok=True)  # Ensure the directory exists

    def _cleanup_old_folders(self):
        """Checks and deletes expired folders."""
        while True:
            try:
                current_time = time.time()
                for folder in os.listdir(self.temp_dir):
                    folder_path = os.path.join(self.temp_dir, folder)
                    if os.path.isdir(folder_path):
                        last_access_time = os.stat(folder_path).st_atime

                        if current_time - last_access_time > self.expiration_time:
                            shutil.rmtree(folder_path, ignore_errors=True)
                            print(f"Deleted expired folder: {folder_path}")
            except Exception as e:
                print(f"Error during cleanup: {e}")

            time.sleep(self.cleanup_interval)  # Run cleanup at the specified interval

    def start_cleanup(self):
        """Starts the cleanup process in a background thread."""
        cleanup_thread = threading.Thread(target=self._cleanup_old_folders, daemon=True)
        cleanup_thread.start()
