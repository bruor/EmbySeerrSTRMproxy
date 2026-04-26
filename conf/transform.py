import json
import logging
from mitmproxy import http

# The fake 1080p stream we inject to satisfy Jellyseerr's resolution check
FAKE_MEDIA_STREAM = {
    "Type": "Video",
    "Codec": "h264",
    "Width": 1920,
    "Height": 1080,
    "Index": 0,
    "IsDefault": True,
    "DisplayTitle": "1080p - h264 (STRM Proxy)"
}

def is_seerr_client(flow: http.HTTPFlow) -> bool:
    """Check if the request comes from Jellyseerr/Overseerr."""
    auth_header = flow.request.headers.get("Authorization", "")
    x_auth_header = flow.request.headers.get("X-Emby-Authorization", "")
    combined = (auth_header + " " + x_auth_header).lower()
    return 'client="seerr"' in combined or 'client="overseerr"' in combined

def has_valid_video_stream(item: dict) -> bool:
    """Check if the item already has a valid probed video stream."""
    media_sources = item.get("MediaSources", [])
    if not media_sources:
        return False
        
    for source in media_sources:
        streams = source.get("MediaStreams", [])
        for stream in streams:
            if stream.get("Type") == "Video" and stream.get("Width", 0) > 0:
                return True
    return False

def inject_fake_media_info(item: dict):
    """Injects the fake MediaSource and MediaStream into the item dictionary."""
    fake_source = {
        "Id": item.get("Id", ""),
        "Path": item.get("Path", ""),
        "Protocol": "File",
        "Type": "Default",
        "Name": "STRM",
        "MediaStreams": [FAKE_MEDIA_STREAM]
    }
    
    # Overwrite the MediaSources and top-level MediaStreams arrays
    item["MediaSources"] = [fake_source]
    item["MediaStreams"] = [FAKE_MEDIA_STREAM]

def process_item(item: dict) -> int:
    """Processes a single item, injecting metadata if it qualifies."""
    if not isinstance(item, dict):
        return 0
        
    path = item.get("Path", "")
    if not path.lower().endswith(".strm"):
        return 0
        
    if has_valid_video_stream(item):
        return 0
        
    inject_fake_media_info(item)
    return 1

def response(flow: http.HTTPFlow):
    """The main mitmproxy hook. Called for every HTTP response."""
    
    # 1. Filter by endpoint Path (only hit Items and Episodes endpoints)
    path = flow.request.path.lower()
    if "/items" not in path and "/episodes" not in path:
        return

    # 2. Filter by Client (ignore normal Emby players)
    if not is_seerr_client(flow):
        return

    # 3. Ensure the response is JSON
    content_type = flow.response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        return

    # 4. Parse, Mutate, and Re-encode
    try:
        # Load the raw JSON string from the response payload
        data = json.loads(flow.response.content)
        injected_count = 0
        
        # Determine if it's a list response (QueryResult) or single item (BaseItemDto)
        if "Items" in data and isinstance(data["Items"], list):
            for item in data["Items"]:
                injected_count += process_item(item)
        elif "Id" in data and "Path" in data:
            injected_count += process_item(data)
            
        # Re-commit the changes back to the HTTP Response body if we changed anything
        if injected_count > 0:
            logging.info(f"[Seerr-Strm-Proxy] Injected fake 1080p metadata into {injected_count} STRM item(s) on {path}")
            flow.response.text = json.dumps(data)
            
    except Exception as e:
        logging.error(f"[Seerr-Strm-Proxy] Failed to process JSON payload: {e}")
