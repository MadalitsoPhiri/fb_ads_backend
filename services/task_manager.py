import os
import signal
import logging
from threading import Lock
from utils.error_handler import emit_error  

# Global storage for task tracking
upload_tasks = {}  # Tracks active upload tasks
tasks_lock = Lock()  # Thread-safe lock for shared resources
canceled_tasks = set()  # Set of canceled task IDs
process_pids = {}  # Maps task IDs to process PIDs

class TaskCanceledException(Exception):
    """Custom exception raised when a task is canceled."""
    pass

def add_task(task_id):
    """
    Adds a task to the upload tracking list.

    Args:
        task_id (str): Unique identifier for the task.
    """
    with tasks_lock:
        if task_id in upload_tasks:
            logging.warning(f"Task {task_id} already exists.")
        else:
            upload_tasks[task_id] = True
            process_pids[task_id] = []
            logging.info(f"Task {task_id} added successfully.")

def check_cancellation(task_id):
    """
    Checks if a task has been marked for cancellation.
    If the task is canceled, it raises a `TaskCanceledException` to halt execution.

    Args:
        task_id (str): The unique identifier of the task.

    Raises:
        TaskCanceledException: If the task has been canceled.
    """
    with tasks_lock:
        if task_id in canceled_tasks:
            logging.info(f"Task {task_id} has been canceled. Raising exception.")
            canceled_tasks.remove(task_id)  # Remove from cancellation set
            raise TaskCanceledException(f"Task {task_id} has been canceled")

def cancel_task(task_id):
    """
    Cancels an active task by marking it as canceled and terminating its associated processes.

    Args:
        task_id (str): The unique identifier of the task to cancel.

    Returns:
        dict: Response message indicating the task cancellation status.
    """
    try:
        logging.info(f"Received request to cancel task: {task_id}")

        with tasks_lock:
            if task_id in canceled_tasks:
                logging.info(f"Task {task_id} was already canceled.")
                return {"message": f"Task {task_id} was already canceled."}

            # Mark task as canceled
            canceled_tasks.add(task_id)

            # If the task exists in the upload queue, mark it as stopped
            if task_id in upload_tasks:
                upload_tasks[task_id] = False  # Stop processing

                # Terminate any associated processes
                for pid in process_pids.get(task_id, []):
                    try:
                        os.kill(pid, signal.SIGTERM)  # Send termination signal
                        logging.info(f"Terminated process {pid} for task {task_id}.")
                    except ProcessLookupError:
                        logging.warning(f"Process {pid} for task {task_id} not found. It may have already exited.")

                # Remove task from active process tracking
                process_pids.pop(task_id, None)
                logging.info(f"Task {task_id} successfully marked for cancellation.")

            # Notify the frontend via SocketIO that the task was canceled
            emit_error(f"Task {task_id} has been canceled.", task_id)

        return {"message": f"Task {task_id} has been canceled."}

    except Exception as e:
        logging.error(f"Error while canceling task {task_id}: {e}")
        emit_error(f"Error canceling task {task_id}: {e}", task_id)
        return {"error": "Failed to cancel task due to internal error."}
