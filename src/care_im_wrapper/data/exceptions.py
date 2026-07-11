class DataFetchError(Exception):
    """Base for all data fetching errors."""


class PermissionDeniedError(DataFetchError):
    """Actor lacks permission for this resource."""


class NoDataError(DataFetchError):
    """Query succeeded but returned no records."""


class MissingContextError(DataFetchError):
    """Required context (e.g. active_patient_external_id for staff) is not set."""


class InvalidQueryError(DataFetchError):
    """Caller-supplied input (e.g. a search query) fails validation before any lookup runs."""
