import uuid

from care.utils.tests.test_utils import OverrideCache  # noqa: F401 # pyright: ignore
from django.test import override_settings


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
