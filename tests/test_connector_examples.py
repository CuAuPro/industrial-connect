import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.mqtt import decode_reading

ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_example_persists_sample_then_reads_without_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SQLITE_DATABASE", str(tmp_path / "example.db"))
    from examples.sqlite import main

    monkeypatch.setattr(
        sys, "argv", ["sqlite", "--initialize", "--record", "--temperature", "73.2"]
    )
    main()
    assert "73.2" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["sqlite"])
    main()
    assert capsys.readouterr().out.count("73.2") == 1


@pytest.mark.parametrize(
    "module",
    [
        "sqlite",
        "postgres",
        "oracle",
        "mssql",
        "questdb",
        "mqtt",
        "mqtt_callback",
        "opcua",
        "oracle_plsql",
        "logging_setup",
        "graylog",
    ],
)
def test_connector_examples_help_does_not_require_configuration(module):
    completed = subprocess.run(
        [sys.executable, "-m", f"examples.{module}", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


@pytest.mark.parametrize(
    "reading",
    [
        [],
        {"equipment_id": "MILL_1", "temperature_c": True, "observed_at": "2026-01-01T00:00:00Z"},
        {
            "equipment_id": "MILL_1",
            "temperature_c": float("nan"),
            "observed_at": "2026-01-01T00:00:00Z",
        },
        {
            "equipment_id": "MILL_1",
            "temperature_c": 10**1000,
            "observed_at": "2026-01-01T00:00:00Z",
        },
        {"equipment_id": "MILL_1", "temperature_c": 72.5, "observed_at": "2026-01-01T00:00:00"},
    ],
)
def test_mqtt_example_rejects_invalid_telemetry(reading):
    with pytest.raises(ValueError):
        decode_reading(json.dumps(reading).encode())


def test_mqtt_example_normalizes_timestamp_to_utc():
    reading = decode_reading(
        b'{"equipment_id":"MILL_1","temperature_c":72.5,"observed_at":"2026-01-01T01:00:00+01:00"}'
    )
    assert reading["observed_at"] == "2026-01-01T00:00:00+00:00"


def test_examples_import_without_opening_connections(monkeypatch):
    import industrial_connect._optional as optional

    def unexpected_import(name):
        pytest.fail(f"Example imported optional driver {name}")

    monkeypatch.setattr(optional, "import_module", unexpected_import)
    for name in (
        "sqlite",
        "postgres",
        "oracle",
        "mssql",
        "questdb",
        "mqtt",
        "mqtt_callback",
        "opcua",
        "oracle_plsql",
    ):
        importlib.reload(importlib.import_module(f"examples.{name}"))
