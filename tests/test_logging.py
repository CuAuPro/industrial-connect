import json
import logging
import zlib

import pytest

from industrial_connect.logging import get_logger, setup_logging, shutdown_logging


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    level = root.level
    yield
    shutdown_logging()
    root.setLevel(level)


def test_module_logger_created_before_setup_uses_console_and_file(tmp_path, capsys):
    logger = get_logger("example.equipment.reader")
    path = tmp_path / "collector.log"
    setup_logging("collector", file=path)
    logger.info("Reading %.1f", 72.5, extra={"equipment_id": "MILL_1"})
    assert "INFO example.equipment.reader Reading 72.5" in capsys.readouterr().err
    text = path.read_text(encoding="utf-8")
    assert "INFO example.equipment.reader Reading 72.5" in text
    assert not text.startswith("{")


def test_connector_module_events_share_configured_file(tmp_path):
    from industrial_connect.db import SqliteClient, SqliteConfig

    path = tmp_path / "collector.log"
    setup_logging("collector", console=False, file=path)
    with SqliteClient(SqliteConfig(database=":memory:")) as db:
        db.query("SELECT 1")
    text = path.read_text(encoding="utf-8")
    assert "industrial_connect.db.sqlite SQLite connection opened" in text
    assert "industrial_connect.db.sqlite SQLite connection closed" in text


def test_graylog_uses_gelf_json_and_console_file_stay_text(tmp_path, capsys, monkeypatch):
    driver = pytest.importorskip("graypy")
    packets = []
    monkeypatch.setattr(
        driver.GELFUDPHandler, "send", lambda self, payload: packets.append(payload)
    )
    path = tmp_path / "collector.log"
    setup_logging("collector", file=path, graylog_host="unused.invalid")
    get_logger("example.reader").info(
        "Reading %.1f", 72.5, extra={"equipment_id": "MILL_1", "temperature_c": 72.5}
    )
    assert len(packets) == 1
    payload = json.loads(zlib.decompress(packets[0]))
    assert payload["short_message"] == "Reading 72.5"
    assert payload["facility"] == "collector"
    assert payload["_logger"] == "example.reader"
    assert payload["_equipment_id"] == "MILL_1"
    assert payload["_temperature_c"] == 72.5
    assert payload["level"] == 6
    assert "INFO example.reader Reading 72.5" in capsys.readouterr().err
    assert "INFO example.reader Reading 72.5" in path.read_text(encoding="utf-8")


def test_reconfigure_and_shutdown_preserve_foreign_root_handlers(tmp_path):
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        setup_logging("first", console=False, file=tmp_path / "first.log")
        old = next(h for h in root.handlers if getattr(h, "_industrial_connect", False))
        setup_logging("second", console=False, file=tmp_path / "second.log")
        assert old.stream is None
        get_logger("example.worker").warning("One event")
        assert "One event" not in (tmp_path / "first.log").read_text()
        assert (tmp_path / "second.log").read_text().count("One event") == 1
        shutdown_logging()
        assert foreign in root.handlers
        assert not any(getattr(h, "_industrial_connect", False) for h in root.handlers)
    finally:
        root.removeHandler(foreign)


def test_failed_configuration_preserves_working_outputs(tmp_path):
    root = logging.getLogger()
    path = tmp_path / "collector.log"
    setup_logging("collector", console=False, file=path)
    before, level = list(root.handlers), root.level
    with pytest.raises(OSError):
        setup_logging("collector", file=tmp_path / "missing" / "collector.log")
    assert list(root.handlers) == before and root.level == level
    with pytest.raises((TypeError, ValueError)):
        setup_logging("collector", level="invalid-level", file=path)
    assert list(root.handlers) == before and root.level == level
    get_logger("example.reader").info("Still logging")
    assert "Still logging" in path.read_text()


def test_connector_errors_do_not_log_sql_or_driver_secrets(tmp_path):
    from industrial_connect.db import SqliteClient, SqliteConfig
    from industrial_connect.errors import OperationError

    path = tmp_path / "collector.log"
    setup_logging("collector", console=False, file=path)
    with SqliteClient(SqliteConfig(database=":memory:")) as db:
        with pytest.raises(OperationError):
            db.query("SELECT * FROM private_secret_table")
    text = path.read_text()
    assert "Database operation failed" in text
    assert "private_secret_table" not in text
