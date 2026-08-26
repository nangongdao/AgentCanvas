"""Execution scheduling errors shared by engine capability mixins."""


class EngineShuttingDown(RuntimeError):
    """Raised when start/resume is called after shutdown has begun."""


class ExecutionConcurrencyLimit(RuntimeError):
    """Raised when all process-local execution slots are occupied."""


__all__ = ["EngineShuttingDown", "ExecutionConcurrencyLimit"]
