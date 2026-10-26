from concurrent.futures import ThreadPoolExecutor

import pytest

from industrial_connect.db import (
    OracleClient,
    OracleConfig,
    PostgresClient,
    PostgresConfig,
    QuestDbClient,
    QuestDbConfig,
    SqliteClient,
    SqliteConfig,
    SqlServerClient,
    SqlServerConfig,
    close_all_databases,
    close_database,
    get_database,
    init_database,
)
from industrial_connect.errors import ConnectionError, OperationError


@pytest.fixture(autouse=True)
def empty_registry():
    close_all_databases()
    yield
    close_all_databases()


def test_identity_independent_databases_and_reinitialization():
    production = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    reporting = init_database("reporting", SqliteClient(SqliteConfig(database=":memory:")))
    assert production is get_database("production")
    assert reporting is get_database("reporting")
    production.execute("CREATE TABLE readings(value INT)")
    production.execute("INSERT INTO readings VALUES (?)", (1,))
    with pytest.raises(OperationError):
        reporting.query("SELECT * FROM readings")
    close_database("production")
    close_database("production")
    with pytest.raises(KeyError, match="production.*init_database"):
        get_database("production")
    assert get_database("reporting") is reporting
    with pytest.raises(ConnectionError, match="closed"):
        production.query("SELECT 1")
    replacement = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    assert replacement is not production


@pytest.mark.parametrize("name", ["", " ", "\t\n", None, 123])
def test_invalid_names(name):
    client = SqliteClient(SqliteConfig(database=":memory:"))
    try:
        with pytest.raises(ValueError, match="name"):
            init_database(name, client)
        with pytest.raises(ValueError, match="name"):
            get_database(name)
        with pytest.raises(ValueError, match="name"):
            close_database(name)
    finally:
        client.close()


def test_duplicate_names_and_client_aliases_are_rejected_without_closing_clients():
    first, second = (
        SqliteClient(SqliteConfig(database=":memory:")),
        SqliteClient(SqliteConfig(database=":memory:")),
    )
    try:
        init_database("production", first)
        with pytest.raises(ValueError, match="already initialized"):
            init_database("production", second)
        with pytest.raises(ValueError, match="another name"):
            init_database("alias", first)
        assert second.query("SELECT 1 AS value") == [{"value": 1}]
        assert get_database("production") is first
        with pytest.raises(KeyError):
            get_database("alias")
    finally:
        second.close()


def test_registration_is_lazy_for_each_query_client(monkeypatch):
    import industrial_connect._optional as optional

    def unexpected_import(name):
        pytest.fail(f"Registration imported optional driver {name}")

    monkeypatch.setattr(optional, "import_module", unexpected_import)
    pg_config = PostgresConfig(host="localhost", database="db", user="u", password="secret")
    clients = [
        OracleClient(OracleConfig(dsn="localhost/service", user="u", password="secret")),
        PostgresClient(pg_config),
        SqlServerClient(
            SqlServerConfig(server="localhost", database="db", user="u", password="secret")
        ),
        SqliteClient(SqliteConfig(database=":memory:")),
        QuestDbClient(QuestDbConfig(conf="wss::addr=localhost:9000;")),
    ]
    for index, client in enumerate(clients):
        assert init_database(str(index), client) is client
        assert get_database(str(index)) is client
    close_all_databases()


def test_invalid_and_closed_clients_are_not_registered():
    with pytest.raises(TypeError, match="QuestDB client"):
        init_database("invalid", object())
    client = SqliteClient(SqliteConfig(database=":memory:"))
    client.close()
    with pytest.raises(ConnectionError, match="closed"):
        init_database("closed", client)
    with pytest.raises(KeyError):
        get_database("closed")


class FailingCloseClient(SqliteClient):
    def __init__(self):
        super().__init__(SqliteConfig(database=":memory:"))
        self.failure = RuntimeError("close failed")
        self.fail_close = True

    def close(self):
        if self.fail_close:
            raise self.failure
        super().close()


def test_failed_close_retains_entry_for_retry():
    client = init_database("production", FailingCloseClient())
    try:
        with pytest.raises(RuntimeError) as error:
            close_database("production")
        assert error.value is client.failure
        assert get_database("production") is client
    finally:
        client.fail_close = False
    close_database("production")
    with pytest.raises(KeyError):
        get_database("production")


def test_close_all_attempts_every_client_and_groups_failures():
    first = init_database("first", FailingCloseClient())
    healthy = init_database("healthy", SqliteClient(SqliteConfig(database=":memory:")))
    last = init_database("last", FailingCloseClient())
    try:
        with pytest.raises(ExceptionGroup) as error:
            close_all_databases()
        assert error.value.exceptions == (first.failure, last.failure)
        assert any("first" in note for note in first.failure.__notes__)
        assert any("last" in note for note in last.failure.__notes__)
        assert get_database("first") is first
        assert get_database("last") is last
        with pytest.raises(KeyError):
            get_database("healthy")
        with pytest.raises(ConnectionError, match="closed"):
            healthy.query("SELECT 1")
    finally:
        first.fail_close = last.fail_close = False
    close_all_databases()
    close_all_databases()


def test_concurrent_lookups_return_same_instance():
    client = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: get_database("production"), range(100)))
    assert all(result is client for result in results)


def test_concurrent_duplicate_registrations_have_one_winner():
    def register(_):
        candidate = SqliteClient(SqliteConfig(database=":memory:"))
        try:
            init_database("production", candidate)
            return candidate
        except ValueError:
            candidate.close()
            return None

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(register, range(30)))
    winners = [client for client in results if client is not None]
    assert len(winners) == 1
    assert get_database("production") is winners[0]
