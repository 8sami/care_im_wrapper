"""Unit test package for care_im_wrapper.

Importing this package blocks outbound HTTP to the messaging provider for the rest of the
test run.

It is needed because nothing else stops it. ``config.settings.test`` inherits
``PLUGIN_CONFIGS`` wholesale from ``config.settings.base``, so a developer's real provider
credentials are live during tests, and ``WhatsAppClient._send`` refuses to send only when
credentials are *absent* -- with valid ones it goes straight to ``httpx.post``. A test that
forgets to patch its sender therefore delivers a real message to a real phone number, and
the only thing standing between the suite and that outcome is per-test patching discipline.

The guard replaces ``httpx``'s request functions with ones that raise on a provider URL and
pass everything else through untouched, so it cannot affect unrelated HTTP in the wider
suite. Tests that exercise the transport patch
``care_im_wrapper.messaging.whatsapp.httpx.post`` themselves; that patch shadows this guard
for the test's duration and restores it afterwards.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx


class LiveProviderCallError(RuntimeError):
    """An unpatched outbound call to the messaging provider escaped the test suite."""


def _provider_hosts() -> set[str]:
    """Hosts that mean a real provider, read at call time so an override applies."""
    from care_im_wrapper.settings import plugin_settings

    hosts = {"graph.facebook.com"}
    configured = urlsplit(str(plugin_settings.WHATSAPP_API_URL)).hostname
    if configured:
        hosts.add(configured)
    return hosts


def _guard(method: str, original: Any) -> Any:
    def _checked(url: Any, *args: Any, **kwargs: Any) -> Any:
        host = urlsplit(str(url)).hostname
        if host in _provider_hosts():
            raise LiveProviderCallError(
                f"{method} {url} would reach the live messaging provider. A test must patch "
                f"its sender -- see care_im_wrapper/tests/__init__.py."
            )
        return original(url, *args, **kwargs)

    return _checked


httpx.post = _guard("POST", httpx.post)
httpx.get = _guard("GET", httpx.get)
