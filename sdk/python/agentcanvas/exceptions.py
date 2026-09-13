"""Exception classes for AgentCanvas SDK."""

from __future__ import annotations


class AgentCanvasError(Exception):
    """Base exception for all AgentCanvas SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        if self.status_code:
            return f"HTTP {self.status_code}: {self.message}"
        return self.message


class AuthenticationError(AgentCanvasError):
    """Raised when API authentication fails."""



class NotFoundError(AgentCanvasError):
    """Raised when a resource is not found."""



class ValidationError(AgentCanvasError):
    """Raised when request validation fails."""



class RequestTimeoutError(AgentCanvasError):
    """Raised when a request times out."""



class RateLimitError(AgentCanvasError):
    """Raised when rate limit is exceeded."""



class ServerError(AgentCanvasError):
    """Raised when the server returns a 5xx error."""

