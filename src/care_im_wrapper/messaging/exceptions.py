class WhatsAppError(Exception):
    """Base exception for WhatsApp API errors."""


class WhatsAppPairRateLimitError(WhatsAppError):
    """Raised when Meta's pair rate limit is hit (error code 131056)."""


class WhatsAppBadRequestError(WhatsAppError):
    """Raised for permanent 4xx errors (excluding 429/131056)."""


class WhatsAppServerError(WhatsAppError):
    """Raised for 5xx errors."""


class WhatsAppNetworkError(WhatsAppError):
    """Raised for network/timeout issues."""


class WhatsAppTemplateNotConfiguredError(WhatsAppError):
    """Raised when a template's variable_mapping has not been configured."""


class OutboundRateLimitedError(Exception):
    """
    Raised by messaging.registry.send_message when a send would exceed the provider's
    minimum interval between messages to the same recipient (see core.rate_limit
    .is_outbound_rate_limited). Provider-agnostic and raised before any API call is
    attempted, unlike WhatsAppPairRateLimitError which is Meta's own after-the-fact
    rejection (error code 131056).
    """
