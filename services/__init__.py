# services/__init__.py

# Import campaign-related functions
from .campaign_service import (
    create_campaign,
    get_campaign_budget_optimization,
    is_campaign_budget_optimized,
    find_campaign_by_id
)

# Import task-related functions
from .task_manager import (
    check_cancellation,
    cancel_task
)

from file_service import (
    get_all_video_files,
    get_all_image_files,
    clean_temp_files
)

# Expose all functions for easier imports
__all__ = [
    "create_campaign",
    "get_campaign_budget_optimization",
    "is_campaign_budget_optimized",
    "find_campaign_by_id",
    "check_cancellation",
    "cancel_task",
    "get_all_video_files",
    "get_all_image_files",
    "clean_temp_files"
]
