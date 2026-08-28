from __future__ import annotations

from typing import Any

import environ
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from rest_framework.settings import perform_import

from care_im_wrapper.core.choices import Provider

PLUGIN_NAME = "care_im_wrapper"

env = environ.Env()


class PluginSettings:  # pragma: no cover
    """Plugin settings, accessed as attributes."""

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
        """This method handles the validation of the plugin settings."""
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
    "PATIENT_SEARCH_MIN_QUERY_LENGTH": 3,  # minimum chars before staff patient lookup runs a query
    # Generic channel caps, for a provider that has not described itself. Deliberately the
    # most restrictive reading: an unknown provider is safer under-filled than rejected.
    "DEFAULT_MAX_MESSAGE_CHARS": 4096,
    "DEFAULT_MIN_SEND_INTERVAL_SECONDS": 0,
    "DEFAULT_MAX_INTERACTIVE_ROWS": 10,
    "DEFAULT_PREVIEW_LINE_LIMIT": 20,  # lines a client shows before folding behind "Read more"
    "DEFAULT_INTERACTIVE_BODY_CHAR_LIMIT": 1024,
    "DEFAULT_INTERACTIVE_HEADER_CHAR_LIMIT": 60,
    "DEFAULT_INTERACTIVE_FOOTER_CHAR_LIMIT": 60,
    "DEFAULT_SECTION_TITLE_CHAR_LIMIT": 20,
    "DEFAULT_ROW_TITLE_CHAR_LIMIT": 20,
    "DEFAULT_ROW_DESCRIPTION_CHAR_LIMIT": 72,
    "DEFAULT_BUTTON_TITLE_CHAR_LIMIT": 20,
    "DEFAULT_TEMPLATE_PARAMETER_CHAR_LIMIT": 1024,
    "WHATSAPP_MIN_SEND_INTERVAL_SECONDS": 6,
    # Ceiling for the doubling backoff applied when a provider rejects a send as too
    # frequent for one recipient pair (see core.rate_limit.note_provider_pair_limit).
    "PAIR_RATE_LIMIT_MAX_BACKOFF_SECONDS": 300,
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
    "SESSION_IDLE_TIMEOUT_SECONDS": 30 * 60,
    "RATE_LIMIT_WINDOW_SECONDS": 60,  # rolling window for inbound rate limiting
    "RATE_LIMIT_MAX_MESSAGES": 10,  # max inbound messages per window per phone number
    "DEBOUNCE_SECONDS": 2,  # delay before processing; resets on each new message in the burst
    "INBOUND_TASK_TIME_LIMIT_SECONDS": 60,
    "TASK_MAX_RETRIES": 3,  # max celery retry attempts for transient failures
    "TASK_RETRY_DELAY_SECONDS": 60,  # seconds between celery retries
    "DISPATCH_CLAIM_STALE_SECONDS": 900,
    "MESSAGE_DEDUP_TIMEOUT_SECONDS": 300,  # how long to remember a seen raw message ID (Meta replays up to ~5 min)
    "DATA_CACHE_TIMEOUT_SECONDS": 90,
    "PHONE_NUMBER_MASK_PREFIX_LEN": 4,
    "PHONE_NUMBER_MASK_SUFFIX_LEN": 3,
    "WHATSAPP_INTERACTIVE_BODY_CHAR_LIMIT": 1024,
    "WHATSAPP_ROW_TITLE_CHAR_LIMIT": 20,
    "WHATSAPP_SECTION_TITLE_CHAR_LIMIT": 20,
    "WHATSAPP_HEADER_CHAR_LIMIT": 60,
    "WHATSAPP_FOOTER_CHAR_LIMIT": 60,
    "WHATSAPP_TEMPLATE_PARAMETER_CHAR_LIMIT": 1024,
    "PAGING_FOOTER_RESERVE_CHARS": 160,
    "WHATSAPP_PREVIEW_LINE_LIMIT": 20,
    "PAGING_FOOTER_RESERVE_LINES": 4,
    "DATA_PAGE_MIN_RECORDS": 1,
    "WHATSAPP_LIST_ROW_LIMIT": 10,  # max rows across all sections of one interactive list
    # Fallback channel when a recipient has no prior ConversationSession to consult.
    "NOTIFICATION_DEFAULT_PROVIDER": Provider.WHATSAPP.value,
    # Beat sweep interval (seconds); real-time dispatch happens via on_commit, this is a safety net.
    "NOTIFICATION_DISPATCH_INTERVAL_SECONDS": 120,
    # Beat sweep interval (seconds) for syncing template approval status from Meta.
    "TEMPLATE_SYNC_INTERVAL_SECONDS": 21600,
    "NOTIFICATION_FAILURE_ERROR_MAX_CHARS": 2000,
    "NOTIFICATION_FAILURE_TRACEBACK_MAX_CHARS": 8000,
    # Which NotificationTrigger.slug a booking status transition fires.
    "APPOINTMENT_TRIGGER_SLUGS": {
        "booked": "appointment_confirmed",
        "cancelled": "appointment_cancelled",
        "rescheduled": "appointment_rescheduled",
        "noshow": "appointment_no_show",
        "checked_in": "appointment_checked_in",
        "in_consultation": "appointment_in_consultation",
        "fulfilled": "appointment_fulfilled",
    },
    "PATIENT_TRIGGER_SLUGS": {
        "registered": "patient_registered",
        "discharged": "patient_discharged",
    },
    "BILLING_TRIGGER_SLUGS": {
        "invoice_issued": "invoice_issued",
        "invoice_cancelled": "invoice_cancelled",
        "payment_recorded": "payment_recorded",
    },
    "APPOINTMENT_REMINDER_TRIGGER_SLUG": "appointment_reminder",
    "WAIT_TIME_TRIGGER_SLUG": "wait_time_update",
    "APPOINTMENT_REMINDER_LEAD_SECONDS": 24 * 60 * 60,
    "APPOINTMENT_REMINDER_SCAN_INTERVAL_SECONDS": 15 * 60,
    "WAIT_TIME_MINUTES_PER_TOKEN": 5,
    "NOTIFICATION_TEMPLATE_VARIABLE_MAPPINGS": {
        "appointment_update": {
            "header_status": "{{ status }}",
            "patient_name": "{{ object.patient.name }}",
            "doctor_name": "{{ doctor_name }}",
            "date": "{{ object.token_slot.start_datetime|date('%d %b %Y') }}",
            "time": "{{ object.token_slot.start_datetime|time }}",
            "location_or_link": "{{ object.token_slot.resource.facility.name }}",
            "status": "{{ status }}",
        },
        "document_ready_update": {
            "header_document_type": "{{ document_type|replace('_', ' ')|title }}",
            "patient_name": "{{ object.patient.name }}",
            "document_type": "{{ document_type|replace('_', ' ')|title }}",
            "sr_name": "{{ object.service_request.title }}",
            "sr_created_at_date": "{{ object.service_request.created_date|date('%d %b %Y') }}",
            # The DocumentLink token, sent as the URL button's one positional parameter.
            # The base it is appended to lives in the template's button definition in the
            # provider console, not here -- the wrapper only ever pulls templates. Point
            # it at the care_fe document page, e.g. https://<care_fe>/public/documents/{{1}}.
            "url_suffix": "{{ document_url_suffix }}",
        },
        "patient_updates": {
            "header_action": "{{ header_action }}",
            "patient_name": "{{ object.name }}",
            "patient_id": "{{ patient_id }}",
            "action": "{{ action }}",
            "date_and_time": "{{ date_and_time }}",
        },
        "payment_status": {
            "header_status": "{{ header_status }}",
            "patient_name": "{{ patient_name }}",
            "amount": "{{ amount }}",
            "patient_account_name": "{{ patient_account_name }}",
            "status": "{{ status }}",
            "invoice_number": "{{ invoice_number }}",
        },
        # Invoice-shaped counterpart to payment_status, taking the identical variables.
        "invoice_status": {
            "header_status": "{{ header_status }}",
            "patient_name": "{{ patient_name }}",
            "amount": "{{ amount }}",
            "patient_account_name": "{{ patient_account_name }}",
            "status": "{{ status }}",
            "invoice_number": "{{ invoice_number }}",
        },
        # related_object: TokenBooking -- the same shape appointment_update already uses.
        "event_reminder": {
            "event_header": "{{ event_header }}",
            "patient_name": "{{ object.patient.name }}",
            "event": "{{ event }}",
            # Handler-supplied for the same reason as appointment_update above.
            "doctor_name": "{{ doctor_name }}",
            "date": "{{ object.token_slot.start_datetime|date('%d %b %Y') }}",
            "location_or_link": "{{ object.token_slot.resource.facility.name }}",
            "time": "{{ object.token_slot.start_datetime|time }}",
        },
        "wait_time_update": {
            "header_event": "{{ event }}",
            "patient_name": "{{ object.patient.name }}",
            "event": "{{ event }}",
            "service_name": "{{ service_name }}",
            "date": "{{ object.queue.date|date('%d %b %Y') }}",
            "waiting_time": "{{ waiting_time }}",
        },
    },
    "ENCOUNTER_REPORT_REUSE_SECONDS": 15 * 60,
    "DOCUMENT_LINK_RATE_LIMIT_WINDOW_SECONDS": 60,
    "DOCUMENT_LINK_RATE_LIMIT_MAX": 30,
    # Validity window for a DocumentLink token.
    "DOCUMENT_LINK_TTL_SECONDS": 60 * 60 * 24 * 7,  # 7 days
    # Presign TTL per request -- short, since the token is the durable capability.
    "DOCUMENT_PRESIGN_TTL_SECONDS": 60 * 5,  # 5 minutes
    "DOCUMENT_LINK_BASE_URL": "",
    # Public origin of the care_fe that serves the patient-facing document page.
    # Blank falls back to CURRENT_DOMAIN.
    "DOCUMENT_PAGE_BASE_URL": "",
}


plugin_settings = PluginSettings(PLUGIN_NAME, defaults=DEFAULTS, required_settings=REQUIRED_SETTINGS)


@receiver(setting_changed)
def reload_plugin_settings(*args, **kwargs) -> None:
    setting = kwargs["setting"]
    if setting == "PLUGIN_CONFIGS":
        plugin_settings.reload()
