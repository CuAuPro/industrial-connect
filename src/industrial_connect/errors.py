"""Public errors. Original driver exceptions are retained in ``__cause__``."""

from collections.abc import Callable
from sys import exception


class IndustrialConnectError(Exception):
    """Base library error."""


class DependencyError(IndustrialConnectError, ImportError):
    """An optional connector dependency is missing."""


class ConnectionError(IndustrialConnectError):
    """A connection, pool, or shutdown operation failed."""


class OperationError(IndustrialConnectError):
    """A query, write, or protocol operation failed."""


def _cleanup(action: Callable[[], None], message: str) -> bool:
    """Keep the original failure when resource cleanup fails during unwinding."""
    active_error = exception()
    try:
        action()
    except Exception as exc:
        if active_error is not None:
            active_error.add_note(message)
        else:
            raise ConnectionError(message) from exc
        return False
    return True
