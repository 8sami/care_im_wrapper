"""Send failures, in the two vocabularies the code needs.

The *semantic* bases (`TransientSendError`, `PermanentSendError`) are what the agnostic
layers catch: `tasks.py` decides whether to spend a retry on the strength of these alone,
so a new provider gets correct retry handling by raising them, without `tasks.py` learning
its name. The *provider* classes below name the concrete failure for logs and tests.

A provider's exception inherits from both: its own marker (`WhatsAppError`) and the
semantic base that says what the caller should do about it.
"""


class SendError(Exception):
    """Base for every outbound send failure, whatever the provider."""


class TransientSendError(SendError):
    """A failure worth retrying: network trouble, a timeout, a provider 5xx, or the
    provider's own after-the-fact pacing rejection."""


class PermanentSendError(SendError):
    """A failure that will never succeed on retry -- a malformed request, or a template
    whose configuration cannot produce a valid message. Record it and stop."""


class PairRateLimitError(TransientSendError):
    """The provider rejected the send for exceeding its own per-recipient rate limit.
    Transient, but worth naming: the useful retry delay is the provider's send interval,
    not the generic backoff."""


class WhatsAppError(SendError):
    """Marker for a failure that came from the WhatsApp/Meta provider."""


class WhatsAppPairRateLimitError(WhatsAppError, PairRateLimitError):
    """Raised when Meta's pair rate limit is hit (error code 131056)."""


class WhatsAppBadRequestError(WhatsAppError, PermanentSendError):
    """Raised for permanent 4xx errors (excluding 429/131056)."""


class WhatsAppServerError(WhatsAppError, TransientSendError):
    """Raised for 5xx errors."""


class WhatsAppNetworkError(WhatsAppError, TransientSendError):
    """Raised for network/timeout issues."""


class WhatsAppTemplateNotConfiguredError(WhatsAppError, PermanentSendError):
    """Raised when a template's variable_mapping has not been configured."""


class OutboundRateLimitedError(Exception):
    """
    Raised by messaging.registry.send_message when a send would exceed the provider's
    minimum interval between messages to the same recipient (see core.rate_limit
    .is_outbound_rate_limited). Provider-agnostic and raised before any API call is
    attempted, unlike PairRateLimitError which is the provider's own after-the-fact
    rejection.
    """
