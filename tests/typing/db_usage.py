"""Static API checks; these functions are never executed against a database."""

from typing import Any, assert_type

import oracledb

from industrial_connect.db import (
    OracleClient,
    OracleConfig,
    OracleResult,
    OracleSession,
    PostgresClient,
    PostgresConfig,
    QuestDbClient,
    QuestDbConfig,
    Row,
    SqliteClient,
    SqliteConfig,
    SqlServerClient,
    SqlServerConfig,
    get_database,
    init_database,
)
from industrial_connect.mqtt import MqttConfig
from industrial_connect.opcua import OpcUaConfig


def oracle_types(client: OracleClient) -> None:
    assert_type(OracleConfig.from_env(env_file=".env"), OracleConfig)
    assert_type(init_database("production", client), OracleClient)
    assert_type(get_database("production"), Any)
    assert_type(client.config, OracleConfig)
    with client as same_client:
        assert_type(same_client, OracleClient)
        with same_client.transaction() as session:
            assert_type(session, OracleSession)
            result = session.call(
                "begin :total := :input; open :rows for select 1 from dual; end;",
                {"input": 1},
                {"total": oracledb.DB_TYPE_NUMBER, "rows": oracledb.DB_TYPE_CURSOR},
            )
            assert_type(result, OracleResult)
            assert_type(result.cursors, dict[str, list[Row]])
        assert_type(
            client.call(
                "begin :value := 'text'; end;", out_binds={"value": oracledb.DB_TYPE_VARCHAR}
            ),
            OracleResult,
        )


def other_database_types(
    postgres: PostgresClient,
    sqlite: SqliteClient,
    mssql: SqlServerClient,
    questdb: QuestDbClient,
) -> None:
    assert_type(PostgresConfig.from_env(), PostgresConfig)
    assert_type(SqliteConfig.from_env(database=":memory:"), SqliteConfig)
    assert_type(SqlServerConfig.from_env(prefix="REPORTING_"), SqlServerConfig)
    assert_type(MqttConfig.from_env(), MqttConfig)
    assert_type(OpcUaConfig.from_env(), OpcUaConfig)
    assert_type(postgres.config, PostgresConfig)
    assert_type(init_database("reporting", postgres), PostgresClient)
    assert_type(init_database("local", sqlite), SqliteClient)
    assert_type(sqlite.config, SqliteConfig)
    assert_type(mssql.config, SqlServerConfig)
    assert_type(postgres.query("select %s as value", (1,)), list[Row])
    assert_type(sqlite.query("select :value as value", {"value": 1}), list[Row])
    assert_type(mssql.execute("update readings set value=%s", (1,)), int)
    assert_type(questdb.query("select value from readings"), list[Row])
    with sqlite.transaction() as tx:
        assert_type(tx.executemany("insert into readings values (?)", [(1,), (2,)]), int)


def questdb_types(client: QuestDbClient) -> None:
    assert_type(QuestDbConfig.from_env(), QuestDbConfig)
    assert_type(init_database("historian", client), QuestDbClient)
    assert_type(client.execute("create table readings(value long)"), int | None)
    assert_type(client.query("select $1 as value", [42]), list[Row])
