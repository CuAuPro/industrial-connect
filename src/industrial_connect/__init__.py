"""Independent connectors; importing this package has no I/O side effects."""

import logging as _logging

from .errors import ConnectionError, DependencyError, IndustrialConnectError, OperationError

_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__version__ = "0.1.0"
__all__ = ["ConnectionError", "DependencyError", "IndustrialConnectError", "OperationError"]
