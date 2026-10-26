from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from sys import exception
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

from .._optional import require
from ..config import EnvConfig
from ..errors import ConnectionError, _cleanup
from ..logging import get_logger
from ._base import DatabaseClient

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class PostgresConfig(EnvConfig):
    _env_prefix = "PG"

    host: str
    database: str
    user: str
    password: str = field(repr=False)
    port: int = 5432
    connect_timeout: int = 10
    sslmode: str = "verify-full"
    sslrootcert: str | None = None
    pool: bool = False
    pool_min: int = 1
    pool_max: int = 5
    pool_timeout: float = 10.0

    def __post_init__(self):
        self._validate_numbers()
        if not self.host or not self.database or not self.user:
            raise ValueError("host, database and user are required")
        if not 0 < self.port < 65536 or self.connect_timeout <= 0 or self.pool_timeout <= 0:
            raise ValueError("Invalid port or timeout")
        if not 0 <= self.pool_min <= self.pool_max or self.pool_max < 1:
            raise ValueError("Invalid pool bounds")
        if self.sslmode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("Invalid PostgreSQL sslmode")


class PostgresClient(DatabaseClient):
    config: PostgresConfig

    def __init__(self, config: PostgresConfig) -> None:
        super().__init__(config)
        self._pool = None
        self._lock = RLock()

    def _kwargs(self) -> dict[str, Any]:
        c = self.config
        values = dict(
            host=c.host,
            port=c.port,
            dbname=c.database,
            user=c.user,
            password=c.password,
            connect_timeout=c.connect_timeout,
            sslmode=c.sslmode,
        )
        if c.sslrootcert:
            values["sslrootcert"] = c.sslrootcert
        return values

    def _connect(self) -> psycopg.Connection:
        logger.debug("Opening PostgreSQL connection")
        return require("psycopg", "postgres").connect(**self._kwargs())

    @contextmanager
    def _connection(self) -> Generator[psycopg.Connection, None, None]:
        if not self.config.pool:
            with super()._connection() as connection:
                yield connection
            return
        with self._lock:
            self._check_open()
            if self._pool is None:
                pool_module = require("psycopg_pool", "postgres")
                try:
                    self._pool = pool_module.ConnectionPool(
                        kwargs=self._kwargs(),
                        min_size=self.config.pool_min,
                        max_size=self.config.pool_max,
                        timeout=self.config.pool_timeout,
                        open=False,
                    )
                    self._pool.open()
                    logger.info("PostgreSQL pool opened")
                except Exception as exc:
                    if self._pool is not None:
                        _cleanup(
                            self._pool.close, "PostgreSQL pool initialization cleanup also failed"
                        )
                        self._pool = None
                    raise ConnectionError("Could not open PostgreSQL pool") from exc
            pool = self._pool
        try:
            connection = pool.getconn(timeout=self.config.pool_timeout)
        except Exception as exc:
            logger.warning("Could not acquire PostgreSQL pooled connection")
            raise ConnectionError("Could not acquire PostgreSQL pooled connection") from exc
        try:
            yield connection
        finally:
            active_error = exception()
            try:
                pool.putconn(connection)
            except Exception as exc:
                if active_error is not None:
                    active_error.add_note(
                        "Returning the PostgreSQL connection to its pool also failed"
                    )
                else:
                    raise ConnectionError("Could not return PostgreSQL pooled connection") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._pool is not None:
                try:
                    self._pool.close()
                    logger.info("PostgreSQL pool closed")
                except Exception as exc:
                    raise ConnectionError("Could not close PostgreSQL pool") from exc
            self._closed = True
