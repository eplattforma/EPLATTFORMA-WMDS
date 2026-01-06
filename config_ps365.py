"""
PS365 API Configuration
Centralized configuration for PS365 API integration
"""
import os

# === PS365 API CONFIG ===
PS365_BASE_URL = os.getenv('PS365_BASE_URL', '').rstrip('/')
PS365_TOKEN = os.getenv('PS365_TOKEN', '')

# Page size for paging through items from PS365
# PS365 items API enforces maximum page_size of 100 (note: different limit than customers API)
PS365_PAGE_SIZE = int(os.getenv('PS365_PAGE_SIZE', '100'))

def validate_config():
    """Validate that required PS365 config is set"""
    if not PS365_BASE_URL or not PS365_TOKEN:
        raise ValueError(
            "PS365_BASE_URL and PS365_TOKEN environment variables must be set. "
            "Please configure these in your .env file or Replit secrets."
        )
