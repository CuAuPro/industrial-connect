from dataclasses import replace
from types import SimpleNamespace

import pytest

from industrial_connect.db import (
    OracleConfig,
    PostgresConfig,
    SqliteClient,
    SqliteConfig,
    SqlServerConfig,
)
from industrial_connect.errors import OperationError
from industrial_connect.mqtt import MqttClient, MqttConfig
from industrial_connect.opcua import OpcUaClient, OpcUaConfig


@pytest.mark.parametrize(
    "config,field",
    [
        (SqliteConfig(), "timeout"),
        (PostgresConfig(host="h", database="d", user="u", password="s"), "pool_timeout"),
        (OracleConfig(dsn="d", user="u", password="s"), "connect_timeout"),
        (SqlServerConfig(server="s", database="d", user="u", password="s"), "query_timeout"),
        (MqttConfig(host="h", client_id="id"), "timeout"),
        (
            OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy="None"),
            "reconnect_interval",
        ),
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_programmatic_configs_reject_nonfinite_numbers(config, field, value):
    with pytest.raises(ValueError, match=field):
        replace(config, **{field: value})


@pytest.mark.parametrize("capacity", [1.5, True, float("inf")])
def test_queue_capacity_must_be_an_integer(capacity):
    with pytest.raises(ValueError):
        MqttClient(MqttConfig(host="h", client_id="id", notification_capacity=capacity))


def test_sqlite_enforces_foreign_keys_and_rolls_back():
    with SqliteClient(SqliteConfig(database=":memory:")) as db:
        db.execute("CREATE TABLE equipment(id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE readings(equipment_id INTEGER REFERENCES equipment(id))")
        with pytest.raises(OperationError):
            db.execute("INSERT INTO readings VALUES (?)", (99,))
        assert db.query("SELECT * FROM readings") == []
        db.execute("INSERT INTO equipment VALUES (?)", (99,))
        db.execute("INSERT INTO readings VALUES (?)", (99,))
        assert db.query("SELECT * FROM readings") == [{"equipment_id": 99}]


def test_sqlite_reopens_after_failed_rollback(tmp_path):
    with SqliteClient(SqliteConfig(database=str(tmp_path / "readings.db"))) as db:
        db.execute("CREATE TABLE readings(value INTEGER)")
        original = db._conn

        class BrokenRollback:
            def __getattr__(self, name):
                return getattr(original, name)

            def rollback(self):
                raise RuntimeError("rollback failed")

        db._conn = BrokenRollback()
        with pytest.raises(OperationError) as error:
            with db.transaction() as tx:
                tx.execute("INSERT INTO readings VALUES (?)", (1,))
                tx.execute("INSERT INTO missing VALUES (1)")
        assert "rollback also failed" in " ".join(error.value.__notes__)
        assert db._conn is None
        assert db.query("SELECT * FROM readings") == []


@pytest.mark.parametrize("kind", ["sqlite", "mqtt", "opcua"])
def test_context_cleanup_preserves_application_error(monkeypatch, kind):
    if kind == "sqlite":
        client = SqliteClient(SqliteConfig(database=":memory:"))
    elif kind == "mqtt":
        client = MqttClient(MqttConfig(host="h", client_id="id"))
    else:
        client = OpcUaClient(
            OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy="None")
        )
    if kind != "sqlite":
        monkeypatch.setattr(client, "connect", lambda: None)

    def fail():
        raise OSError("cleanup failed")

    monkeypatch.setattr(client, "close", fail)
    original = RuntimeError("application failed")
    with pytest.raises(RuntimeError) as error:
        with client:
            raise original
    assert error.value is original
    assert "cleanup also failed" in " ".join(original.__notes__)


def test_mqtt_startup_failure_survives_failed_cleanup(monkeypatch):
    client = MqttClient(MqttConfig(host="h", client_id="id"))
    original = RuntimeError("loop start failed")

    def start():
        raise original

    def cleanup():
        raise OSError("disconnect failed")

    raw = SimpleNamespace(
        connect_async=lambda *a: None, loop_start=start, disconnect=cleanup, loop_stop=cleanup
    )
    monkeypatch.setattr(client, "_make_client", lambda: raw)
    from industrial_connect.errors import ConnectionError

    with pytest.raises(ConnectionError) as error:
        client.start()
    assert error.value.__cause__ is original
    assert len(original.__notes__) == 2


def test_mqtt_failed_shutdown_can_be_retried(monkeypatch):
    from test_mqtt import make_client

    from industrial_connect.errors import ConnectionError

    client, raw = make_client(monkeypatch)
    client.start()

    def fail():
        raise OSError("loop shutdown failed")

    raw.loop_stop = fail
    with pytest.raises(ConnectionError):
        client.close()
    assert client.connection_status == "CLOSED"
    raw.loop_stop = lambda: None
    client.close()
    assert client._client is None


def test_failed_opcua_subscription_preserves_error_during_cleanup(monkeypatch):
    from test_opcua import make_client

    client, raw = make_client(monkeypatch)
    client.connect()
    original = ValueError("monitored items failed")

    def subscribe(*args, **kwargs):
        raise original

    def cleanup():
        raise OSError("subscription deletion failed")

    monkeypatch.setattr(
        raw,
        "create_subscription",
        lambda *args: SimpleNamespace(
            subscribe_data_change=subscribe,
            delete=cleanup,
        ),
    )
    with pytest.raises(OperationError) as error:
        client.subscribe(["node"])
    assert error.value.__cause__ is original
    assert "cleanup also failed" in " ".join(original.__notes__)
    client.close()


@pytest.mark.parametrize(
    "options", [{"max_bytes": float("nan")}, {"backup_count": 1.5}, {"graylog_port": True}]
)
def test_logging_rejects_invalid_rotation_settings_before_configuring_handlers(options):
    import logging

    from industrial_connect.logging import setup_logging

    logger = logging.getLogger("production-failure-test")
    before = list(logger.handlers)
    with pytest.raises(ValueError):
        setup_logging(logger.name, **options)
    assert logger.handlers == before
