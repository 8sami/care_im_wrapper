class DocumentUnavailableError(Exception):
    """Raised when a document link cannot be issued: no active Template for the
    encounter's facility, or generation failed. Callers degrade gracefully."""
