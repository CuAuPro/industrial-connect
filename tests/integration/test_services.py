"""These tests create/drop dedicated tables in disposable test services only."""

import os
import time
from queue import Queue
from uuid import uuid4

import pytest

from industrial_connect.db import (
    OracleClient,
    OracleConfig,
    PostgresClient,
    PostgresConfig,
    QuestDbClient,
    QuestDbConfig,
    SqlServerClient,
    SqlServerConfig,
)
from industrial_connect.mqtt import MqttClient, MqttConfig

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("service", ["postgres", "oracle", "mssql"], indirect=True)
def test_database_roundtrip_and_rollback(service, test_password):
    host = os.environ.get("IC_TEST_HOST", "127.0.0.1")
    table = "ic_test_" + uuid4().hex[:12]
    clients = {
        "postgres": lambda: PostgresClient(
            PostgresConfig(
                host=host,
                port=15432,
                database="industrial",
                user="appuser",
                password=test_password,
                sslmode="disable",
                pool=True,
            )
        ),
        "oracle": lambda: OracleClient(
            OracleConfig(
                dsn=f"{host}:11521/FREEPDB1",
                user="appuser",
                password=test_password,
                pool=True,
            )
        ),
        "mssql": lambda: SqlServerClient(
            SqlServerConfig(
                server=host,
                port=11433,
                database="master",
                user="sa",
                password=test_password,
                encryption="off",
            )
        ),
    }
    bind = ":value" if service == "oracle" else "%s"

    def params(value):
        return {"value": value} if service == "oracle" else (value,)

    with clients[service]() as client:
        client.execute(f"CREATE TABLE {table} (value integer)")
        try:
            client.execute(f"INSERT INTO {table} (value) VALUES ({bind})", params(1))
            with pytest.raises(RuntimeError):
                with client.transaction() as tx:
                    tx.execute(f"INSERT INTO {table} (value) VALUES ({bind})", params(2))
                    raise RuntimeError("rollback")
            rows = client.query(f"SELECT value FROM {table}")
            assert len(rows) == 1
            assert next(iter(rows[0].values())) == 1
            if service == "oracle":
                result = client.call(
                    "begin :result := :input + 1; end;", {"input": 41}, {"result": int}
                )
                assert result.out_binds["result"] == 42
        finally:
            client.execute(f"DROP TABLE {table}")


@pytest.mark.parametrize("service", ["mqtt"], indirect=True)
@pytest.mark.parametrize("delivery", ["queue", "callback"])
def test_mqtt_roundtrip(service, delivery):
    topic = "ic-test/" + uuid4().hex
    config = MqttConfig(
        host=os.environ.get("IC_TEST_HOST", "127.0.0.1"),
        port=11883,
        client_id="ic-test-" + uuid4().hex,
        tls=False,
    )
    received = Queue()
    with MqttClient(config, on_message=received.put if delivery == "callback" else None) as client:
        client.subscribe(topic, qos=1)
        # Publishing on the same connection follows SUBSCRIBE in wire order.
        client.publish(topic, b"roundtrip", qos=1, wait=True)
        message = (
            received.get(timeout=10)
            if delivery == "callback"
            else client.get_notification(timeout=10)
        )
        assert message.payload == b"roundtrip"
        assert message.topic == topic


@pytest.mark.parametrize("service", ["questdb"], indirect=True)
def test_questdb_ingest_query(service, test_password):
    host = os.environ.get("IC_TEST_HOST", "127.0.0.1")
    table = "ic_test_" + uuid4().hex
    with QuestDbClient(
        QuestDbConfig(
            conf=f"ws::addr={host}:19000;auto_flush=off;username=admin;password={test_password};",
        )
    ) as db:
        try:
            db.execute(
                f"create table {table} (timestamp timestamp, site symbol, value long) "
                "timestamp(timestamp) partition by day wal"
            )
            db.row(
                table, symbols={"site": "test"}, columns={"value": 42}, timestamp_ns=time.time_ns()
            )
            db.flush()
            deadline = time.monotonic() + 15
            while True:
                rows = db.query(f"select value from {table} where site = $1", ["test"])
                if rows:
                    break
                if time.monotonic() > deadline:
                    pytest.fail("QuestDB WAL row did not become visible")
                time.sleep(0.1)
            assert rows == [{"value": 42}]
            assert db.query(f"select value from {table} where site = $1", ["absent"]) == []
        finally:
            db.execute(f"drop table {table}")
