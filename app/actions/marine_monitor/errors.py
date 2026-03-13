"""Custom exceptions for Marine Monitor API client."""


class MarineMonitorClientError(Exception):
    """Base exception for Marine Monitor API client errors."""

    def __init__(self, message=None, response=None):
        super().__init__(message)
        self.response = response


class MarineMonitorServiceUnreachable(MarineMonitorClientError):
    """Raised when the Marine Monitor service is unavailable."""

    def __init__(self, message="Marine Monitor service is currently unavailable.", response=None):
        super().__init__(message, response)


class MarineMonitorAuthenticationError(MarineMonitorClientError):
    """Raised when authentication fails."""

    def __init__(self, message="Invalid API key or unauthorized access.", response=None):
        super().__init__(message, response)


class MarineMonitorRateLimitError(MarineMonitorClientError):
    """Raised when rate limit is exceeded."""

    def __init__(self, message="Rate limit exceeded.", response=None):
        super().__init__(message, response)
