import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from industrial_connect.mqtt import MqttPacket, Uom


@pytest.mark.parametrize("value", ["running", 42, 72.5, True, False, None])
def test_packet_preserves_json_scalar_types(value):
    packet = MqttPacket(value=value, timestamp=datetime(2026, 10, 28, 10, tzinfo=UTC))
    payload = json.loads(packet.to_json())
    assert payload == {"value": value, "timestamp": "2026-10-28T10:00:00Z"}
    assert type(payload["value"]) is type(value)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (Uom.mm, "mm"),
        (Uom.tonne, "t"),
        (Uom.percent, "%"),
        (Uom.degC, "degC"),
        ("widgets/hour", "widgets/hour"),
    ],
)
def test_packet_serializes_enum_and_custom_units(unit, expected):
    assert json.loads(MqttPacket(value=1, uom=unit).to_json())["uom"] == expected


def test_units_support_lookup_and_iteration():
    assert Uom("t") is Uom.tonne
    assert Uom("%") is Uom.percent
    assert all(isinstance(unit, str) for unit in Uom)


def test_packet_defaults_to_current_utc_timestamp():
    before = datetime.now(UTC)
    packet = MqttPacket(value=1)
    after = datetime.now(UTC)
    assert before <= packet.timestamp <= after
    assert packet.timestamp.tzinfo is UTC
    assert json.loads(packet.to_json())["timestamp"].endswith("Z")


def test_packet_normalizes_offset_and_preserves_microseconds():
    timestamp = datetime(2026, 10, 28, 11, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    packet = MqttPacket(value=1, timestamp=timestamp)
    assert json.loads(packet.to_json())["timestamp"] == "2026-10-28T10:00:00.123456Z"
    assert packet.timestamp == timestamp


@pytest.mark.parametrize("value", [[], {}, b"raw", object()])
def test_packet_rejects_unsupported_values(value):
    with pytest.raises(TypeError, match="value"):
        MqttPacket(value=value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_packet_rejects_nonfinite_values(value):
    with pytest.raises(ValueError, match="finite"):
        MqttPacket(value=value)


def test_packet_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        MqttPacket(value=1, timestamp=datetime(2026, 10, 28))


def test_packet_rejects_non_datetime_timestamp():
    with pytest.raises(TypeError, match="timestamp"):
        MqttPacket(value=1, timestamp="2026-10-28T10:00:00Z")


@pytest.mark.parametrize("unit", ["", " ", "\t\n"])
def test_packet_rejects_blank_units(unit):
    with pytest.raises(ValueError, match="blank"):
        MqttPacket(value=1, uom=unit)


def test_packet_rejects_non_string_unit():
    with pytest.raises(TypeError, match="uom"):
        MqttPacket(value=1, uom=42)


def test_packet_is_frozen_and_keyword_only():
    packet = MqttPacket(value=1)
    with pytest.raises(FrozenInstanceError):
        packet.value = 2
    with pytest.raises(TypeError):
        MqttPacket(1)
