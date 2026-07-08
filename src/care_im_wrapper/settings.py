from __future__ import annotations

from typing import Any

import environ
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from rest_framework.settings import perform_import

PLUGIN_NAME = "care_im_wrapper"  # was causing circular import

env = environ.Env()


class PluginSettings:  # pragma: no cover
    """
    A settings object that allows plugin settings to be accessed as
    properties. For example:

        from plugin.settings import plugin_settings
        print(plugin_settings.API_KEY)

    Any setting with string import paths will be automatically resolved
    and return the class, rather than the string literal.

    """

    def __init__(
        self,
        plugin_name: str | None = None,
        defaults: dict[str, Any] | None = None,
        import_strings: set[str] | None = None,
        required_settings: set[str] | None = None,
    ) -> None:
        if not plugin_name:
            raise ValueError("Plugin name must be provided")
        self.plugin_name = plugin_name
        self.defaults = defaults or {}
        self.import_strings = import_strings or set()
        self.required_settings = required_settings or set()
        self._cached_attrs = set()
        self._user_settings: dict[str, Any] | None = None
        self.validate()

    def __getattr__(self, attr: str) -> Any:
        if attr not in self.defaults:
            raise AttributeError(f"Invalid setting: '{attr}'")

        # Try to find the setting from user settings, then from environment variables
        val = self.defaults[attr]
        try:
            val = self.user_settings[attr]
        except KeyError:
            try:
                val = env(attr, cast=type(val))
            except environ.ImproperlyConfigured:
                # Fall back to defaults
                pass

        # Coerce import strings into classes
        if attr in self.import_strings:
            val = perform_import(val, attr)

        self._cached_attrs.add(attr)
        setattr(self, attr, val)
        return val

    @property
    def user_settings(self) -> dict[str, Any]:
        if self._user_settings is None:
            result: dict[str, Any] = getattr(settings, "PLUGIN_CONFIGS", {}).get(self.plugin_name, {})
            self._user_settings = result
            return result
        return self._user_settings

    def validate(self) -> None:
        """
        This method handles the validation of the plugin settings.
        It could be overridden to provide custom validation logic.

        the base implementation checks if all the required settings are truthy.
        """
        for setting in self.required_settings:
            if not getattr(self, setting):
                raise ImproperlyConfigured(
                    f'The "{setting}" setting is required. '
                    f'Please set the "{setting}" in the environment or the {PLUGIN_NAME} plugin config.'
                )

    def reload(self) -> None:
        """
        Deletes the cached attributes so that they will be recomputed next time they are accessed.
        """
        for attr in self._cached_attrs:
            delattr(self, attr)
        self._cached_attrs.clear()
        self._user_settings = None


REQUIRED_SETTINGS = {
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
}

DEFAULTS = {
    "WHATSAPP_MESSAGE_CHAR_LIMIT": 4096,
    "WHATSAPP_TITLE_TRUNCATE": 20,
    "WHATSAPP_DESCRIPTION_TRUNCATE": 72,
    "DATA_FETCH_LIMIT": 10,
    "WHATSAPP_MIN_SEND_INTERVAL_SECONDS": 6,
    "WHATSAPP_API_URL": "https://graph.facebook.com/v25.0",
    "WHATSAPP_PHONE_NUMBER_ID": "",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN": "",
    "WHATSAPP_ACCESS_TOKEN": "",
    "WHATSAPP_BUSINESS_ACCOUNT_ID": "",
    "WHATSAPP_APP_SECRET": "",  # Meta app secret for HMAC webhook verification
    "MAX_FAILED_ATTEMPTS": 5,  # failed YOB attempts before the session is locked
    "COOLDOWN_MINUTES": 30,  # duration of the cooldown period
    "RATE_LIMIT_WINDOW_SECONDS": 60,  # rolling window for inbound rate limiting
    "RATE_LIMIT_MAX_MESSAGES": 10,  # max inbound messages per window per phone number
    "DEBOUNCE_SECONDS": 2,  # delay before processing; resets on each new message in the burst
    "TASK_EXECUTION_BUFFER_SECONDS": 10,  # margin to allow network timeout before task kill
    "TASK_MAX_RETRIES": 3,  # max celery retry attempts for transient failures
    "TASK_RETRY_DELAY_SECONDS": 60,  # seconds between celery retries
    "MESSAGE_DEDUP_TIMEOUT_SECONDS": 300,  # how long to remember a seen raw message ID (Meta replays up to ~5 min)
    "DATA_CACHE_TIMEOUT_SECONDS": 90,
    "PHONE_NUMBER_MASK_PREFIX_LEN": 4,
    "PHONE_NUMBER_MASK_SUFFIX_LEN": 3,
    "WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT": 1024,
    # Channel used for signal-triggered notifications that have no other channel
    # signal to consult (e.g. a booking's patient has no prior ConversationSession).
    # Must be a value registered in messaging.registry._PROVIDERS.
    "NOTIFICATION_DEFAULT_PROVIDER": "whatsapp",
}


plugin_settings = PluginSettings(PLUGIN_NAME, defaults=DEFAULTS, required_settings=REQUIRED_SETTINGS)


@receiver(setting_changed)
def reload_plugin_settings(*args, **kwargs) -> None:
    setting = kwargs["setting"]
    if setting == "PLUGIN_CONFIGS":
        plugin_settings.reload()
