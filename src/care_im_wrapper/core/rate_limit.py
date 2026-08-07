from django.core.cache import cache

from care_im_wrapper.settings import plugin_settings


def is_rate_limited(subject: str, *, window: int | None = None, max_hits: int | None = None) -> bool:
    """
    Returns True if `subject` has exceeded `max_hits` within `window` seconds.
    Uses a fixed-window counter in Django's cache backend.
    Increments the counter on each call; caller should only call this once per event.

    Defaults are the *inbound message* limits; callers limiting anything else must pass
    their own, so tuning chat throughput doesn't silently retune unrelated endpoints.
    """
    window = int(plugin_settings.RATE_LIMIT_WINDOW_SECONDS) if window is None else window
    max_messages = int(plugin_settings.RATE_LIMIT_MAX_MESSAGES) if max_hits is None else max_hits
    key = f"rate_limit:{subject}"

    if cache.add(key, 1, timeout=window):
        return False

    # If add failed, it means key exists, so we increment. The key can still expire
    # between the add() above and this incr() (TOCTOU) -- Django's cache backends raise
    # ValueError when incrementing a missing key, so treat that race as a fresh window.
    try:
        current_count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=window)
        return False

    # The first message sets the count to 1. We want to allow up to max_messages.
    # So if current_count is 11 and max_messages is 10, we are limited.
    return current_count > max_messages


def note_provider_pair_limit(channel: str, phone_number: str) -> int:
    """Records that the provider refused a send as too frequent for this recipient, and
    returns how long to wait before trying again.

    Our own minimum interval is a guess at the provider's; when the provider contradicts
    it, that guess was wrong for this pair. So the wait doubles on each consecutive
    rejection, and the pacing key is armed for that long -- which makes every *other*
    queued turn for the same reader wait too, instead of each one independently retrying
    into the same wall. The counter expires on its own, so a pair that goes quiet returns
    to normal pacing without anything having to reset it.
    """
    from care_im_wrapper.messaging.registry import get_min_send_interval_seconds

    base = max(1, get_min_send_interval_seconds(channel))
    ceiling = int(plugin_settings.PAIR_RATE_LIMIT_MAX_BACKOFF_SECONDS)
    level_key = f"pair_backoff:{channel}:{phone_number}"

    if cache.add(level_key, 1, timeout=ceiling):
        level = 1
    else:
        try:
            level = cache.incr(level_key)
        except ValueError:
            cache.add(level_key, 1, timeout=ceiling)
            level = 1

    backoff = min(base * 2**level, ceiling)
    cache.set(f"outbound_rate_limit:{channel}:{phone_number}", True, timeout=backoff)
    return backoff


def is_outbound_rate_limited(channel: str, phone_number: str, *, is_urgent: bool = False) -> bool:
    """
    Returns True if a send to phone_number on channel happened within the last
    min_send_interval_seconds (per messaging.registry.get_min_send_interval_seconds).
    Minimum-interval throttle, not a counting window: does not track how many sends
    happened, only whether enough time has passed since the last one.
    is_urgent bypasses the check entirely, with no cache interaction.
    """
    if is_urgent:
        return False

    from care_im_wrapper.messaging.registry import get_min_send_interval_seconds

    interval = get_min_send_interval_seconds(channel)
    key = f"outbound_rate_limit:{channel}:{phone_number}"

    if cache.add(key, True, timeout=interval):
        return False

    return True
