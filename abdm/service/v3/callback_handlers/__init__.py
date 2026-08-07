class CallbackProcessingError(Exception):
    """Raised when an inbound callback cannot be processed (e.g. referenced
    entity missing). Marks the envelope FAILED after retries are exhausted."""
