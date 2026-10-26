import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock

from ..config import EnvConfig
from ..errors import ConnectionError, _cleanup
from ..logging import get_logger
from ._base import DatabaseClient

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class SqliteConfig(EnvConfig):
    _env_prefix = "SQLITE_"

    database: str = "store.db"
    timeout: float = 10.0

    def __post_init__(self):
        self._validate_numbers()
        if not self.database or self.timeout <= 0:
            raise ValueError("database and a positive timeout are required")


class SqliteClient(DatabaseClient):
    """One lazily opened connection, serialized across calling threads."""

    config: SqliteConfig

    def __init__(self, config: SqliteConfig | None = None) -> None:
        super().__init__(config or SqliteConfig())
        self._lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._in_transaction = False

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            self._check_open()
            if self._in_transaction:
                raise ConnectionError(
                    "Use the active session; nested SQLite transactions are unsupported"
                )
            if self._conn is None:
                try:
                    self._conn = sqlite3.connect(
                        self.config.database,
                        timeout=self.config.timeout,
                        check_same_thread=False,
                    )
                    self._conn.execute("PRAGMA foreign_keys=ON")
                    logger.info("SQLite connection opened")
                except Exception as exc:
                    connection, self._conn = self._conn, None
                    if connection is not None:
                        _cleanup(connection.close, "SQLite initialization cleanup also failed")
                    raise ConnectionError("Could not open SQLite database") from exc
            try:
                self._conn.execute("BEGIN")
            except sqlite3.Error as exc:
                raise ConnectionError("Could not begin SQLite transaction") from exc
            self._in_transaction = True
            try:
                yield self._conn
            finally:
                self._in_transaction = False

    def _discard_connection(self, connection: sqlite3.Connection) -> None:
        logger.warning("Discarding SQLite connection after rollback failure")
        self._conn = None
        connection.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._in_transaction:
                raise ConnectionError("Cannot close SQLite during an active transaction")
            if self._conn is not None:
                _cleanup(self._conn.close, "Could not close SQLite connection")
                self._conn = None
                logger.info("SQLite connection closed")
            self._closed = True
