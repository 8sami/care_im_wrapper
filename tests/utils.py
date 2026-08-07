import uuid
from dataclasses import replace
from unittest.mock import patch

from care.utils.tests.test_utils import OverrideCache  # noqa: F401 # pyright: ignore
from django.test import override_settings

from care_im_wrapper.messaging.limits import ChannelLimits, whatsapp_limits


def channel_limits(**overrides) -> ChannelLimits:
    """A provider's real limits with a field or two changed.

    Starting from a real set rather than a hand-built one keeps a test honest about every
    cap it is not exercising, so adding a field to ChannelLimits never silently changes what
    a test was pinning.
    """
    return replace(whatsapp_limits(), **overrides)


def patched_limits(module: str = "care_im_wrapper.conversation.handlers", **overrides):
    """Patches the channel limits a module resolves, for a test that needs a tighter cap
    than the real provider has -- a shorter body, no reply buttons, fewer rows."""
    return patch(f"{module}.get_channel_limits", return_value=channel_limits(**overrides))


def override_test_cache():
    """A local memory cache with a unique per-class LOCATION, like @OverrideCache but
    usable as a real decorator.

    @OverrideCache applied bare never invokes __call__, leaving the class name bound to an
    OverrideCache instance rather than a TestCase -- discovery then collects zero tests
    with no error. Use @override_test_cache() with parens instead.
    """
    return override_settings(
        CACHES={
            "default": {
                "BACKEND": "config.caches.LocMemCache",
                "LOCATION": f"care-test-{uuid.uuid4()}",
            }
        }
    )
