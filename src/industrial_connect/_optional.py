from importlib import import_module
from types import ModuleType

from .errors import DependencyError


def require(module: str, extra: str) -> ModuleType:
    try:
        return import_module(module)
    except ImportError as exc:
        raise DependencyError(
            f'Install "industrial-connect[{extra}]" to use this connector'
        ) from exc
