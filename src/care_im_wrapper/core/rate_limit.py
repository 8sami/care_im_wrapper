from django.core.cache import cache

from care_im_wrapper.settings import plugin_settings


def is_rate_limited(phone_number: str) -> bool:
    """
    Returns True if phone_number has exceeded MAX_MESSAGES in the current window.
    Uses a sliding counter in Django's cache backend.
    Increments the counter on each call; caller should only call this once per message.
    """
    window = plugin_settings.RATE_LIMIT_WINDOW_SECONDS
    max_messages = plugin_settings.RATE_LIMIT_MAX_MESSAGES
    key = f"rate_limit:{phone_number}"

    # Use a simple counter with a fixed window.
    # We use cache.incr which is atomic in Redis.
    # For LocMemCache, it's also fine for single-process.

    if cache.add(key, 1, timeout=window):
        return False

    # If add failed, it means key exists, so we increment.
    current_count = cache.incr(key)

    # The first message sets the count to 1. We want to allow up to max_messages.
    # So if current_count is 11 and max_messages is 10, we are limited.
    return current_count > max_messages
