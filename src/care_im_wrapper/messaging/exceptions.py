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
