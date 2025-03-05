# utils/__init__.py

# Import campaign-related functions
from .error_handler import (
    emit_error
)

# Expose all functions for easier imports
__all__ = [
    "emit_error"
]