"""DB-API session mechanics shared by relational clients, not SQL dialects."""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from sys import exception
from typing import Any, Self

from ..errors import ConnectionError, IndustrialConnectError, OperationError, _cleanup
from ..logging import get_logger
from .types import QueryParams, Row

logger = get_logger(__name__)


class Session:
    """A transaction-scoped connection. Do not share sessions between threads."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._active = True

    @contextmanager
    def _cursor(self) -> Generator[Any, None, None]:
        if not self._active:
            raise OperationError("Transaction session is no longer active")
        cursor = None
        try:
            cursor = self._connection.cursor()
            yield cursor
        except IndustrialConnectError:
            raise
        except Exception as exc:
            logger.warning("Database operation failed")
            raise OperationError("Database operation failed") from exc
        finally:
            if cursor is not None:
                active_error = exception()
                try:
                    cursor.close()
                except Exception as exc:
                    if active_error is not None:
                        active_error.add_note("Database cursor cleanup also failed")
                    else:
                        raise OperationError("Could not close database cursor") from exc

    def query(self, sql: str, params: QueryParams | None = None) -> list[Row]:
        with self._cursor() as cursor:
            cursor.execute(sql, params) if params is not None else cursor.execute(sql)
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def execute(self, sql: str, params: QueryParams | None = None) -> int:
        with self._cursor() as cursor:
            cursor.execute(sql, params) if params is not None else cursor.execute(sql)
            return cursor.rowcount

    def executemany(self, sql: str, params: Sequence[QueryParams]) -> int:
        with self._cursor() as cursor:
            cursor.executemany(sql, params)
            return cursor.rowcount


class DatabaseClient:
    _session_type = Session

    def __init__(self, config: Any) -> None:
        self.config = config
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            raise ConnectionError("Client is closed; create a new client")

    def _connect(self) -> Any:
        raise NotImplementedError("Database clients must implement connection creation")

    def _discard_connection(self, connection: Any) -> None:
        connection.close()

    @contextmanager
    def _connection(self) -> Generator[Any, None, None]:
        self._check_open()
        connection = None
        try:
            connection = self._connect()
        except IndustrialConnectError:
            raise
        except Exception as exc:
            logger.warning("Could not open database connection")
            raise ConnectionError("Could not open database connection") from exc
        try:
            yield connection
        finally:
            active_error = exception()
            try:
                connection.close()
            except Exception as exc:
                if active_error is not None:
                    active_error.add_note("Database connection cleanup also failed")
                else:
                    raise ConnectionError("Could not close database connection") from exc

    @contextmanager
    def transaction(self) -> Generator[Session, None, None]:
        """Commit on successful exit; rollback on error, using one connection."""
        with self._connection() as connection:
            session = self._session_type(connection)
            try:
                yield session
                try:
                    connection.commit()
                except Exception as exc:
                    logger.warning("Database commit failed; outcome may be uncertain")
                    raise OperationError(
                        "Database commit failed; outcome may be uncertain"
                    ) from exc
            except BaseException as error:
                try:
                    connection.rollback()
                except Exception:
                    logger.warning("Database rollback failed; connection will be discarded")
                    error.add_note("Database rollback also failed; connection was discarded")
                    try:
                        self._discard_connection(connection)
                    except Exception:
                        error.add_note("Database connection cleanup also failed")
                raise
            finally:
                session._active = False

    def query(self, sql: str, params: QueryParams | None = None) -> list[Row]:
        with self.transaction() as session:
            return session.query(sql, params)

    def execute(self, sql: str, params: QueryParams | None = None) -> int:
        with self.transaction() as session:
            return session.execute(sql, params)

    def executemany(self, sql: str, params: Sequence[QueryParams]) -> int:
        with self.transaction() as session:
            return session.executemany(sql, params)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(self, *_: Any) -> None:
        _cleanup(self.close, "Database client cleanup also failed")
