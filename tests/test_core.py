import logging
import sys

import pytest

from industrial_connect._optional import require
from industrial_connect.db import (
    OracleConfig,
    PostgresConfig,
    QuestDbConfig,
    SqlServerConfig,
)
from industrial_connect.errors import DependencyError
from industrial_connect.logging import setup_logging, shutdown_logging
from industrial_connect.mqtt import MqttConfig
from industrial_connect.opcua import OpcUaConfig


def test_optional_dependency_error_has_install_instruction(monkeypatch):
    import industrial_connect._optional as module

    def fail(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(module, "import_module", fail)
    with pytest.raises(DependencyError, match=r"industrial-connect\[mqtt\]") as error:
        require("paho.mqtt.client", "mqtt")
    assert isinstance(error.value.__cause__, ModuleNotFoundError)


def test_passwords_excluded_from_config_representations():
    configs = [
        OracleConfig(dsn="secret", user="u", password="secret"),
        PostgresConfig(host="localhost", database="d", user="u", password="secret"),
        SqlServerConfig(server="localhost", database="d", user="u", password="secret"),
        QuestDbConfig(conf="wss::addr=localhost:9000;token=secret;"),
        MqttConfig(host="localhost", client_id="id", password="secret", key_password="secret"),
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            security_policy="None",
            password="secret",
            private_key_password="secret",
        ),
    ]
    assert all("secret" not in repr(config) for config in configs)


def test_logging_replaces_owned_handlers_only(tmp_path):
    name = "industrial-connect-tests"
    logger = logging.getLogger(name)
    foreign = logging.NullHandler()
    logger.addHandler(foreign)
    root_handlers = list(logging.getLogger().handlers)
    root_level = logging.getLogger().level
    path = tmp_path / "app.log"
    try:
        setup_logging(name, console=False, file=path)
        first = next(
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "_industrial_connect", False)
        )
        setup_logging(name, console=False, file=path)
        assert logger.handlers == [foreign]
        assert foreign in logger.handlers
        assert first.stream is None
        logger.info("one message")
        assert path.read_text().count("one message") == 1
        assert all(handler in logging.getLogger().handlers for handler in root_handlers)
        assert (
            sum(
                getattr(handler, "_industrial_connect", False)
                for handler in logging.getLogger().handlers
            )
            == 1
        )
    finally:
        shutdown_logging()
        logging.getLogger().setLevel(root_level)
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_package_import_does_not_import_optional_drivers():
    import subprocess

    code = """
import sys
import industrial_connect.db
import industrial_connect.opcua
import industrial_connect.mqtt
import industrial_connect.logging
assert not ({"psycopg", "oracledb", "pymssql", "questdb", "asyncua", "paho", "graypy", "dotenv"} & sys.modules.keys())
"""
    subprocess.run([sys.executable, "-c", code], check=True)
