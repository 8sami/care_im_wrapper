from __future__ import annotations

from typing import Any

import environ
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from rest_framework.settings import perform_import

from care_im_wrapper.core.choices import Provider

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
    "WHATSAPP_TRUNCATE_RESERVE_CHARS": 20,  # chars reserved for the "... (truncated)" suffix
    "WHATSAPP_DESCRIPTION_TRUNCATE": 72,
    "DATA_FETCH_LIMIT": 10,
    "PATIENT_SEARCH_MIN_QUERY_LENGTH": 3,  # minimum chars before staff patient lookup runs a query
    # Fallbacks used only when messaging.registry is asked about a channel with no registered
    # provider -- not derived from any specific provider's actual limits.
    "DEFAULT_MAX_MESSAGE_CHARS": 4096,
    "DEFAULT_MIN_SEND_INTERVAL_SECONDS": 0,
    "DEFAULT_MAX_INTERACTIVE_ROWS": 10,
    "DEFAULT_MAX_REPLY_BUTTONS": 3,
    "WHATSAPP_MIN_SEND_INTERVAL_SECONDS": 6,
    "WHATSAPP_DEFAULT_LANGUAGE_CODE": "en_US",  # used when a template has no language_code set
    "WHATSAPP_HTTP_TIMEOUT_SECONDS": 10,
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
    # Hard time limit for one inbound turn. A turn can send several messages, so this must
    # cover multiple provider round trips -- a mid-turn kill replays already-sent messages.
    "INBOUND_TASK_TIME_LIMIT_SECONDS": 60,
    "TASK_MAX_RETRIES": 3,  # max celery retry attempts for transient failures
    "TASK_RETRY_DELAY_SECONDS": 60,  # seconds between celery retries
    # How long a dispatch claim is honoured before the sweep reclaims it. Must stay well above
    # a full retry budget (TASK_MAX_RETRIES * TASK_RETRY_DELAY_SECONDS = 180s) to avoid a
    # parallel re-dispatch of a task that is merely retrying.
    "DISPATCH_CLAIM_STALE_SECONDS": 900,
    "MESSAGE_DEDUP_TIMEOUT_SECONDS": 300,  # how long to remember a seen raw message ID (Meta replays up to ~5 min)
    "DATA_CACHE_TIMEOUT_SECONDS": 90,
    "PHONE_NUMBER_MASK_PREFIX_LEN": 4,
    "PHONE_NUMBER_MASK_SUFFIX_LEN": 3,
    "WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT": 1024,
    "WHATSAPP_LIST_ROW_LIMIT": 10,  # max rows across all sections of one interactive list
    "WHATSAPP_REPLY_BUTTON_LIMIT": 3,  # max reply buttons on one interactive message
    # Fallback channel when a recipient has no prior ConversationSession to consult.
    "NOTIFICATION_DEFAULT_PROVIDER": Provider.WHATSAPP.value,
    # Beat sweep interval (seconds); real-time dispatch happens via on_commit, this is a safety net.
    "NOTIFICATION_DISPATCH_INTERVAL_SECONDS": 120,
    # Beat sweep interval (seconds) for syncing template approval status from Meta.
    "TEMPLATE_SYNC_INTERVAL_SECONDS": 21600,
    # Caps on the raw failure detail stored per failed NotificationStatus row. Provider
    # exceptions carry the whole upstream response body, so these bound the JSONB row.
    "NOTIFICATION_FAILURE_ERROR_MAX_CHARS": 2000,
    "NOTIFICATION_FAILURE_TRACEBACK_MAX_CHARS": 8000,
    # Which NotificationTrigger.slug a booking status transition fires.
    "APPOINTMENT_TRIGGER_SLUGS": {
        "booked": "appointment_confirmed",
        "cancelled": "appointment_cancelled",
        "rescheduled": "appointment_rescheduled",
    },
    # NotificationTemplate.slug -> variable_mapping (Meta param name -> Jinja2 expression,
    # rendered via messaging.variables.resolve_variable).
    "NOTIFICATION_TEMPLATE_VARIABLE_MAPPINGS": {
        "appointment_update": {
            "header_status": "{{ status }}",
            "patient_name": "{{ object.patient.name }}",
            "doctor_name": "{{ object.token_slot.resource.user.full_name }}",
            "date": "{{ object.token_slot.start_datetime|date('%d %b %Y') }}",
            "time": "{{ object.token_slot.start_datetime|time }}",
            "location_or_link": "{{ object.token_slot.resource.facility.name }}",
            "status": "{{ status }}",
        },
        "document_ready_update": {
            "patient_name": "{{ object.patient.name }}",
            "document_type": "{{ document_type }}",
            # Resolves to the link's path segment; whatsapp.py prepends the base URL.
            "url_suffix": "{{ document_url_suffix }}",
        },
    },
    # How recently an encounter report must have been generated to be reused instead of
    # rendered again. Short on purpose: the report is a clinical snapshot, so this exists to
    # collapse repeat requests, not to cache the document.
    "ENCOUNTER_REPORT_REUSE_SECONDS": 15 * 60,
    # Per-token throttle on the public document redirect. Distinct from the inbound-chat
    # limits above: a patient legitimately reopens a link several times, and these two
    # limits must be tunable independently.
    "DOCUMENT_LINK_RATE_LIMIT_WINDOW_SECONDS": 60,
    "DOCUMENT_LINK_RATE_LIMIT_MAX": 30,
    # Validity window for a DocumentLink token.
    "DOCUMENT_LINK_TTL_SECONDS": 60 * 60 * 24 * 7,  # 7 days
    # Presign TTL per request -- short, since the token is the durable capability.
    "DOCUMENT_PRESIGN_TTL_SECONDS": 60 * 5,  # 5 minutes
    # Public origin the token route is served from -- scheme + host, no path, e.g.
    # "https://care.example.org". Empty falls back to core's BACKEND_DOMAIN.
    "DOCUMENT_LINK_BASE_URL": "",
}


plugin_settings = PluginSettings(PLUGIN_NAME, defaults=DEFAULTS, required_settings=REQUIRED_SETTINGS)


@receiver(setting_changed)
def reload_plugin_settings(*args, **kwargs) -> None:
    setting = kwargs["setting"]
    if setting == "PLUGIN_CONFIGS":
        plugin_settings.reload()
