from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import oracledb

from .._optional import require
from ..config import EnvConfig
from ..errors import ConnectionError, OperationError, _cleanup
from ..logging import get_logger
from ._base import DatabaseClient, Session
from .types import Row

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class OracleConfig(EnvConfig):
    _env_prefix = "ORACLE_"

    dsn: str = field(repr=False)
    user: str
    password: str = field(repr=False)
    connect_timeout: float = 10.0
    call_timeout_ms: int = 30000
    pool: bool = False
    pool_min: int = 1
    pool_max: int = 5
    pool_timeout_ms: int = 10000

    def __post_init__(self) -> None:
        self._validate_numbers()
        if not self.dsn or not self.user:
            raise ValueError("dsn and user are required")
        if min(self.connect_timeout, self.call_timeout_ms, self.pool_timeout_ms) <= 0:
            raise ValueError("Timeouts must be positive")
        if not 0 <= self.pool_min <= self.pool_max or self.pool_max < 1:
            raise ValueError("Invalid pool bounds")


def init_oracle_thick(lib_dir: str | None = None) -> None:
    """Explicit process-wide opt-in; call before any Oracle connection is opened."""
    driver = require("oracledb", "oracle")
    try:
        driver.init_oracle_client(lib_dir=lib_dir)
    except Exception as exc:
        raise ConnectionError("Could not initialize Oracle Thick mode") from exc


@dataclass
class OracleResult:
    out_binds: dict[str, Any] = field(default_factory=dict)
    cursors: dict[str, list[Row]] = field(default_factory=dict)
    implicit_results: list[list[Row]] = field(default_factory=list)


def _fetch(cursor: oracledb.Cursor) -> list[Row]:
    description = cursor.description
    if description is None:
        raise OperationError("Oracle cursor did not return rows")
    names = [cast(str, column[0]) for column in description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


class OracleSession(Session):
    def call(
        self,
        sql: str,
        binds: Mapping[str, Any] | None = None,
        out_binds: Mapping[str, type | oracledb.DbType | oracledb.DbObjectType | str] | None = None,
    ) -> OracleResult:
        """Execute PL/SQL with Python types, native oracledb types, or legacy type names."""
        driver = require("oracledb", "oracle")
        values = dict(binds or {})
        if set(values) & set(out_binds or {}):
            raise ValueError("Input and output bind names must be distinct")
        with self._cursor() as cursor:
            variables = {}
            for name, type_ in (out_binds or {}).items():
                variable = cursor.var(getattr(driver, type_) if isinstance(type_, str) else type_)
                values[name] = variables[name] = variable
            cursor.execute(sql, values)
            result = OracleResult()
            for name, variable in variables.items():
                value = variable.getvalue()
                if hasattr(value, "fetchall"):
                    try:
                        result.cursors[name] = _fetch(value)
                    finally:
                        _cleanup(value.close, "Oracle REF CURSOR cleanup also failed")
                else:
                    result.out_binds[name] = value
            for implicit in cursor.getimplicitresults():
                try:
                    result.implicit_results.append(_fetch(implicit))
                finally:
                    _cleanup(implicit.close, "Oracle implicit cursor cleanup also failed")
            return result


class OracleClient(DatabaseClient):
    _session_type = OracleSession
    config: OracleConfig

    def __init__(self, config: OracleConfig) -> None:
        super().__init__(config)
        self._lock = RLock()
        self._pool: oracledb.ConnectionPool | None = None

    def _connect(self) -> oracledb.Connection:
        logger.debug("Acquiring Oracle connection")
        driver = require("oracledb", "oracle")
        c = self.config
        kwargs = dict(
            user=c.user,
            password=c.password,
            dsn=c.dsn,
            tcp_connect_timeout=c.connect_timeout,
            retry_count=0,
        )
        with self._lock:
            self._check_open()
            if c.pool and self._pool is None:
                self._pool = driver.create_pool(
                    **kwargs,
                    min=c.pool_min,
                    max=c.pool_max,
                    increment=1,
                    getmode=driver.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=c.pool_timeout_ms,
                )
                logger.info("Oracle pool opened")
            if c.pool:
                assert self._pool is not None
                connection = self._pool.acquire()
            else:
                connection = driver.connect(**kwargs)
        try:
            connection.call_timeout = c.call_timeout_ms
        except Exception:
            _cleanup(connection.close, "Oracle connection initialization cleanup also failed")
            raise
        return connection

    @contextmanager
    def transaction(self) -> Generator[OracleSession, None, None]:
        with super().transaction() as session:
            yield cast(OracleSession, session)

    def call(
        self,
        sql: str,
        binds: Mapping[str, Any] | None = None,
        out_binds: Mapping[str, type | oracledb.DbType | oracledb.DbObjectType | str] | None = None,
    ) -> OracleResult:
        with self.transaction() as session:
            return session.call(sql, binds, out_binds)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._pool is not None:
                try:
                    self._pool.close()
                    logger.info("Oracle pool closed")
                except Exception as exc:
                    raise ConnectionError(
                        "Could not close Oracle pool; check active transactions"
                    ) from exc
            self._closed = True
