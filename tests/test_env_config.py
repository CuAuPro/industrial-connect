import os
from pathlib import Path

import pytest

from industrial_connect.db import (
    OracleClient,
    OracleConfig,
    PostgresConfig,
    QuestDbConfig,
    SqliteConfig,
    SqlServerConfig,
    close_database,
    get_database,
    init_database,
)
from industrial_connect.errors import DependencyError
from industrial_connect.mqtt import MqttConfig
from industrial_connect.opcua import OpcUaConfig


def test_environment_only_parses_numbers_booleans_and_keeps_defaults(monkeypatch):
    monkeypatch.setenv("TEST_PG_HOST", "localhost")
    monkeypatch.setenv("TEST_PG_DATABASE", "reporting")
    monkeypatch.setenv("TEST_PG_USER", "app")
    monkeypatch.setenv("TEST_PG_PASSWORD", "dummy-secret")
    monkeypatch.setenv("TEST_PG_PORT", "15432")
    monkeypatch.setenv("TEST_PG_POOL", "yes")
    monkeypatch.setenv("TEST_PG_POOL_TIMEOUT", "2.5")
    config = PostgresConfig.from_env(prefix="TEST_PG_")
    assert (config.host, config.port, config.pool, config.pool_timeout) == (
        "localhost",
        15432,
        True,
        2.5,
    )
    assert config.sslmode == "verify-full"
    assert config.sslrootcert is None
    assert "dummy-secret" not in repr(config)


def test_empty_optional_environment_values_become_none(monkeypatch):
    monkeypatch.setenv("EMPTY_CA_FILE", "")
    monkeypatch.setenv("EMPTY_PASSWORD", "")
    config = MqttConfig.from_env(prefix="EMPTY_", host="localhost", client_id="test")
    assert config.ca_file is None and config.password is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("TRUE", True),
        ("1", True),
        ("on", True),
        ("False", False),
        ("0", False),
        ("off", False),
        (" no ", False),
    ],
)
def test_boolean_values(monkeypatch, raw, expected):
    monkeypatch.setenv("TEST_MQTT_TLS", raw)
    assert (
        MqttConfig.from_env(prefix="TEST_MQTT_", host="localhost", client_id="test").tls is expected
    )


def test_dotenv_precedence_prefixes_and_no_environment_mutation(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    env_file = tmp_path / "connections.env"
    env_file.write_text(
        'DEMO_DSN=localhost/service\nDEMO_USER=file-user\nDEMO_PASSWORD="quoted $secret # value"\n'
        "DEMO_POOL=true\nDEMO_POOL_MAX=7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEMO_USER", "environment-user")
    monkeypatch.setenv("DEMO_POOL_MAX", "8")
    before = dict(os.environ)
    config = OracleConfig.from_env(env_file=env_file, prefix="DEMO_", pool_max=9)
    assert config.user == "environment-user"
    assert config.password == "quoted $secret # value"
    assert config.pool is True and config.pool_max == 9
    assert dict(os.environ) == before
    assert "quoted $secret" not in repr(config)
    oracle = init_database("env-test", OracleClient(config))
    try:
        assert get_database("env-test") is oracle
        assert oracle._pool is None
    finally:
        close_database("env-test")


@pytest.mark.parametrize(
    "config_type,prefix,values,field,expected",
    [
        (
            OracleConfig,
            "ORACLE_",
            {"DSN": "localhost/svc", "USER": "u", "PASSWORD": "s"},
            "user",
            "u",
        ),
        (
            PostgresConfig,
            "PG",
            {"HOST": "localhost", "DATABASE": "db", "USER": "u", "PASSWORD": "s"},
            "database",
            "db",
        ),
        (
            SqlServerConfig,
            "MSSQL_",
            {"SERVER": "localhost", "DATABASE": "db", "USER": "u", "PASSWORD": "s"},
            "server",
            "localhost",
        ),
        (SqliteConfig, "SQLITE_", {"DATABASE": "test.db"}, "database", "test.db"),
        (
            QuestDbConfig,
            "QUESTDB_",
            {"CONF": "wss::addr=localhost:9000;"},
            "conf",
            "wss::addr=localhost:9000;",
        ),
        (
            MqttConfig,
            "MQTT_",
            {"HOST": "localhost", "CLIENT_ID": "test", "USERNAME": "u"},
            "username",
            "u",
        ),
        (
            OpcUaConfig,
            "OPCUA_",
            {
                "ENDPOINT": "opc.tcp://localhost:4840",
                "SECURITY_POLICY": "None",
                "AUTO_GENERATE_CERTIFICATE": "false",
            },
            "auto_generate_certificate",
            False,
        ),
    ],
)
def test_default_prefixes(config_type, prefix, values, field, expected, monkeypatch):
    for key in list(os.environ):
        if key.startswith(prefix):
            monkeypatch.delenv(key)
    for name, value in values.items():
        monkeypatch.setenv(prefix + name, value)
    assert getattr(config_type.from_env(), field) == expected


@pytest.mark.parametrize(
    "field,value",
    [("PORT", "dummy-secret"), ("POOL", "maybe"), ("POOL_TIMEOUT", "nan"), ("POOL_TIMEOUT", "inf")],
)
def test_invalid_environment_values_do_not_expose_values(monkeypatch, field, value):
    monkeypatch.setenv("BAD_" + field, value)
    with pytest.raises(ValueError, match="BAD_" + field) as error:
        PostgresConfig.from_env(prefix="BAD_", host="h", database="d", user="u", password="s")
    assert value not in str(error.value)
    assert error.value.__suppress_context__


def test_missing_file_missing_fields_and_unknown_overrides(tmp_path):
    with pytest.raises(FileNotFoundError):
        OracleConfig.from_env(env_file=tmp_path / "missing.env")
    with pytest.raises(
        ValueError, match="MISSING_ORACLE_DSN.*MISSING_ORACLE_USER.*MISSING_ORACLE_PASSWORD"
    ):
        OracleConfig.from_env(prefix="MISSING_ORACLE_")
    with pytest.raises(TypeError, match="Unknown.*typo"):
        SqliteConfig.from_env(typo=True)


def test_dotenv_extra_is_required_only_for_files(tmp_path, monkeypatch):
    import industrial_connect.config as module

    env_file = tmp_path / ".env"
    env_file.write_text("SQLITE_DATABASE=:memory:\n", encoding="utf-8")

    def missing(*args):
        raise DependencyError('Install "industrial-connect[env]"')

    monkeypatch.setattr(module, "require", missing)
    assert SqliteConfig.from_env(prefix="NO_DOTENV_").database == "store.db"
    with pytest.raises(DependencyError, match=r"industrial-connect\[env\]"):
        SqliteConfig.from_env(env_file=env_file)


def test_plain_constructor_still_works_and_from_env_does_not_discover_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("SQLITE_DATABASE=not-automatically-loaded.db\n", encoding="utf-8")
    monkeypatch.delenv("SQLITE_DATABASE", raising=False)
    assert SqliteConfig.from_env().database == "store.db"
    assert SqliteConfig(database="plain.db").database == "plain.db"
