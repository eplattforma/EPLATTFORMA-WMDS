import os
import requests
from io import BytesIO
from PIL import Image
import logging
import threading

# Configure base URL for product images
BASE_IMAGE_URL = "https://powersoft365customers.blob.core.windows.net/he353264-step-eplattforma/Items"

# Configure local storage for images
LOCAL_IMAGE_DIR = "static/images"
os.makedirs(LOCAL_IMAGE_DIR, exist_ok=True)

def prefetch_images_for_invoice(item_codes):
    """
    Pre-fetch and cache images for a list of item codes in a background thread.
    This ensures images are ready when the picker navigates to each item.
    """
    def _prefetch():
        for item_code in item_codes:
            try:
                # Just call get_product_image - it handles caching
                get_product_image(item_code)
            except Exception as e:
                logging.error(f"Error pre-fetching image for {item_code}: {e}")
    
    # Run in background thread so it doesn't block the picker
    thread = threading.Thread(target=_prefetch, daemon=True)
    thread.start()
    logging.info(f"Started background image pre-fetch for {len(item_codes)} items")

def get_product_image(item_code):
    """
    Attempts to get a product image from the remote server.
    If successful, the image is resized and stored locally.
    Returns the path to the local image or a placeholder if not found.
    
    Args:
        item_code: The product code to search for
        
    Returns:
        String: Path to the image file (relative to static folder)
    """
    # Check if we already have this image locally
    local_path = f"{LOCAL_IMAGE_DIR}/{item_code}.webp"
    relative_path = f"images/{item_code}.webp"
    
    if os.path.exists(local_path):
        logging.info(f"Image for {item_code} found in local cache")
        return relative_path
    
    # Try to download the image in different formats
    for ext in ['jpg', 'png']:
        try:
            image_url = f"{BASE_IMAGE_URL}/{item_code}.{ext}"
            logging.info(f"Attempting to download image from {image_url}")
            
            response = requests.get(image_url, timeout=5)
            if response.status_code == 200:
                # Process and save the image
                img = Image.open(BytesIO(response.content))
                
                # Calculate new dimensions while maintaining aspect ratio
                width, height = img.size
                new_width = 400
                new_height = int(height * (new_width / width))
                
                # Resize the image - using a simple resize method to avoid version issues
                img = img.resize((new_width, new_height))
                
                # Save as WebP for better compression
                img.save(local_path, 'WEBP', quality=85)
                logging.info(f"Successfully downloaded and processed image for {item_code}")
                return relative_path
                
        except Exception as e:
            logging.error(f"Error downloading image for {item_code}.{ext}: {str(e)}")
    
    # If we get here, no image was found
    logging.warning(f"No image found for {item_code}, using placeholder")
    return "images/image-not-found.png"