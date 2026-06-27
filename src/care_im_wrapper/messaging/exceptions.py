class WhatsAppError(Exception):
    """Base exception for WhatsApp API errors."""


class WhatsAppPairRateLimitError(WhatsAppError):
    """Raised when Meta's pair rate limit is hit (error code 131056)."""
