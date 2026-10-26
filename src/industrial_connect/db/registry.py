"""Named, process-local clients. Initialize before workers; close after they stop."""

from threading import RLock
from typing import Any

from ._base import DatabaseClient
from .questdb import QuestDbClient

_databases: dict[str, DatabaseClient | QuestDbClient] = {}
_lock = RLock()


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Database name must be a nonempty string")


def init_database[ClientT: DatabaseClient | QuestDbClient](name: str, client: ClientT) -> ClientT:
    """Register and return an existing client without opening a connection."""
    _validate_name(name)
    if not isinstance(client, (DatabaseClient, QuestDbClient)):
        raise TypeError("Register a relational database or QuestDB client")
    with _lock:
        if name in _databases:
            raise ValueError(f"Database {name!r} is already initialized")
        if any(registered is client for registered in _databases.values()):
            raise ValueError("This database client is already registered under another name")
        client._check_open()
        _databases[name] = client
        return client


def get_database(name: str) -> Any:
    """Return the actual named client, including its driver-specific methods."""
    _validate_name(name)
    with _lock:
        try:
            return _databases[name]
        except KeyError:
            raise KeyError(
                f"Database {name!r} is not initialized; call init_database() first"
            ) from None


def close_database(name: str) -> None:
    """Close and remove a client. Failed closures stay registered for retry."""
    _validate_name(name)
    with _lock:
        client = _databases.get(name)
        if client is not None:
            client.close()
            del _databases[name]


def close_all_databases() -> None:
    """Attempt every close, retaining failures and reporting them as an ExceptionGroup."""
    errors: list[Exception] = []
    with _lock:
        for name in tuple(_databases):
            try:
                close_database(name)
            except Exception as exc:
                exc.add_note(f"While closing database {name!r}")
                errors.append(exc)
    if errors:
        raise ExceptionGroup("Could not close all registered databases", errors)
