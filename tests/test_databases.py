from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from industrial_connect.db import (
    OracleClient,
    OracleConfig,
    PostgresClient,
    PostgresConfig,
    SqliteClient,
    SqliteConfig,
    SqlServerClient,
    SqlServerConfig,
)
from industrial_connect.db._base import DatabaseClient
from industrial_connect.errors import ConnectionError, OperationError


class FakeCursor:
    description = [("value",)]
    rowcount = 1

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        if sql == "fail":
            raise ValueError("driver failure")

    def executemany(self, sql, params):
        for row in params:
            self.execute(sql, row)

    def fetchall(self):
        return [(42,)]

    def close(self):
        self.connection.cursor_closes += 1


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.commits = self.rollbacks = self.closes = self.cursor_closes = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def test_sqlite_default_store_is_lazy_and_persistent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with SqliteClient() as client:
        assert client.config.database == "store.db"
        assert not (tmp_path / "store.db").exists()
        client.execute("CREATE TABLE readings(value INTEGER)")
        client.execute("INSERT INTO readings VALUES (?)", (42,))
    with SqliteClient() as client:
        assert client.query("SELECT * FROM readings") == [{"value": 42}]


def test_sqlite_database_environment_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SQLITE_DATABASE", "custom.db")
    with SqliteClient(SqliteConfig.from_env()) as client:
        assert client.query("SELECT 1 AS value") == [{"value": 1}]
    assert (tmp_path / "custom.db").exists()
    assert not (tmp_path / "store.db").exists()
    monkeypatch.setenv("SQLITE_DATABASE", ":memory:")
    with SqliteClient(SqliteConfig.from_env()) as client:
        assert client.query("SELECT 1 AS value") == [{"value": 1}]
    assert not (tmp_path / "store.db").exists()


def test_real_sqlite_commit_rollback_and_session_lifetime():
    with SqliteClient(SqliteConfig(database=":memory:")) as client:
        client.execute("create table readings (value integer)")
        assert client.executemany("insert into readings values (?)", [(1,), (2,)]) == 2
        with pytest.raises(OperationError) as error:
            with client.transaction() as session:
                session.execute("insert into readings values (?)", (3,))
                session.execute("insert into missing values (1)")
        assert error.value.__cause__ is not None
        assert client.query("select * from readings") == [{"value": 1}, {"value": 2}]
        with pytest.raises(OperationError, match="no longer active"):
            session.query("select 1")
        assert client.query("select value from readings where value=?", (99,)) == []
    client.close()
    with pytest.raises(ConnectionError, match="closed"):
        client.query("select 1")


def test_sqlite_independent_clients_and_serialized_threads(tmp_path):
    with SqliteClient(SqliteConfig(database=str(tmp_path / "readings.db"))) as client:
        client.execute("create table readings (value integer)")
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(
                workers.map(
                    lambda i: client.execute("insert into readings values (?)", (i,)), range(30)
                )
            )
        assert len(client.query("select * from readings")) == 30
        with client.transaction():
            with pytest.raises(ConnectionError, match="nested"):
                client.query("select 1")
    with SqliteClient(SqliteConfig(database=":memory:")) as other:
        with pytest.raises(OperationError):
            other.query("select * from readings")


def test_sqlite_ddl_rollback():
    with SqliteClient(SqliteConfig(database=":memory:")) as client:
        with pytest.raises(RuntimeError):
            with client.transaction() as tx:
                tx.execute("create table rollback_table (id int)")
                raise RuntimeError("cancel")
        with pytest.raises(OperationError):
            client.query("select * from rollback_table")


def test_session_keeps_one_connection_and_original_error(monkeypatch):
    connection = FakeConnection()
    client = DatabaseClient(None)
    monkeypatch.setattr(client, "_connect", lambda: connection, raising=False)
    with client.transaction() as tx:
        assert tx.query("select %s", (1,)) == [{"value": 42}]
        assert tx.execute("insert", (2,)) == 1
        assert connection.commits == 0
    assert (connection.commits, connection.closes, connection.cursor_closes) == (1, 1, 2)
    with pytest.raises(OperationError) as error:
        client.execute("fail")
    assert isinstance(error.value.__cause__, ValueError)
    assert connection.rollbacks == 1


def test_commit_failure_is_visible_and_rolls_back(monkeypatch):
    connection = FakeConnection()

    def fail():
        raise ValueError("commit failure")

    connection.commit = fail
    client = DatabaseClient(None)
    monkeypatch.setattr(client, "_connect", lambda: connection, raising=False)
    with pytest.raises(OperationError, match="uncertain") as error:
        client.execute("insert")
    assert isinstance(error.value.__cause__, ValueError)
    assert connection.rollbacks == connection.closes == 1


def test_postgres_pool_acquisition_return_and_timeout(monkeypatch):
    import industrial_connect.db.postgres as module

    conn = FakeConnection()

    class Pool:
        def __init__(self, **kwargs):
            assert kwargs["kwargs"]["dbname"] == "db"
            assert kwargs["open"] is False
            self.returned = []
            self.exhausted = False
            self.closed = False

        def open(self):
            pass

        def getconn(self, *, timeout):
            assert timeout == 0.1
            if self.exhausted:
                raise TimeoutError("pool exhausted")
            return conn

        def putconn(self, connection):
            self.returned.append(connection)

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "require", lambda *_: SimpleNamespace(ConnectionPool=Pool))
    config = PostgresConfig(
        host="localhost", database="db", user="u", password="secret", pool=True, pool_timeout=0.1
    )
    with PostgresClient(config) as client:
        assert client._pool is None
        assert client.query("select") == [{"value": 42}]
        assert client._pool.returned == [conn]
        assert conn.closes == 0
        with pytest.raises(OperationError):
            client.execute("fail")
        assert client._pool.returned == [conn, conn]
        client._pool.exhausted = True
        with pytest.raises(ConnectionError) as error:
            client.query("select")
        assert isinstance(error.value.__cause__, TimeoutError)
        pool = client._pool
    assert pool.closed


def test_database_driver_options_and_lazy_constructors(monkeypatch):
    import industrial_connect.db.mssql as mssql
    import industrial_connect.db.oracle as oracle

    conn = FakeConnection()
    options = []
    driver = SimpleNamespace(connect=lambda **kwargs: options.append(kwargs) or conn)
    monkeypatch.setattr(oracle, "require", lambda *_: driver)
    monkeypatch.setattr(mssql, "require", lambda *_: driver)
    oc = OracleClient(OracleConfig(dsn="localhost/service", user="u", password="secret"))
    sc = SqlServerClient(
        SqlServerConfig(server="localhost", database="db", user="u", password="secret")
    )
    assert options == []
    oc.query("select")
    assert conn.call_timeout == 30000
    assert options[-1]["retry_count"] == 0
    sc.query("select")
    assert options[-1]["encryption"] == "require"
    assert options[-1]["as_dict"] is False


@pytest.mark.parametrize(
    "config",
    [
        lambda: PostgresConfig(host="", database="db", user="u", password="s"),
        lambda: SqliteConfig(timeout=0),
        lambda: OracleConfig(dsn="d", user="u", password="s", pool_min=6, pool_max=2),
        lambda: SqlServerConfig(server="s", database="d", user="u", password="s", port=0),
    ],
)
def test_invalid_database_configuration(config):
    with pytest.raises(ValueError):
        config()
