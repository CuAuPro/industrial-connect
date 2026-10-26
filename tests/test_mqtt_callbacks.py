from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from test_mqtt import FakeMqtt

from industrial_connect.errors import ConnectionError, OperationError
from industrial_connect.mqtt import MqttClient, MqttConfig


def setup(monkeypatch, handler, **config):
    client = MqttClient(
        MqttConfig(host="localhost", tls=False, timeout=config.pop("timeout", 1), **config),
        on_message=handler,
    )
    raw = FakeMqtt()
    monkeypatch.setattr(client, "_make_client", lambda: raw)
    return client, raw


def send(client, payload):
    client._on_message(
        None, None, SimpleNamespace(topic="plant/data", payload=payload, qos=1, retain=True)
    )


def test_callback_delivery_is_ordered_off_network_thread_and_exclusive(monkeypatch):
    messages = []
    threads = []
    finished = Event()

    def handler(message):
        messages.append(message)
        threads.append(get_ident())
        if len(messages) == 2:
            finished.set()

    client, raw = setup(monkeypatch, handler)
    assert client._callback_worker is None
    client.start()
    worker = client._callback_worker
    client.start()
    assert client._callback_worker is worker
    try:
        send(client, b"one")
        send(client, b"two")
        assert finished.wait(2)
        assert [message.payload for message in messages] == [b"one", b"two"]
        assert all(
            message.topic == "plant/data" and message.qos == 1 and message.retain
            for message in messages
        )
        assert threads[0] == threads[1] != get_ident()
        assert client.notifications.empty()
        with pytest.raises(OperationError, match="callback mode"):
            client.get_notification(timeout=0.01)
    finally:
        client.close()
    assert not worker.is_alive()
    assert raw.disconnected and raw.stopped
    send(client, b"late")
    assert len(messages) == 2
    client.close()


def test_callback_failure_is_visible_and_next_message_is_processed(monkeypatch):
    finished = Event()
    failure = ValueError("private payload details")

    def handler(message):
        if message.payload == b"bad":
            raise failure
        finished.set()

    client, _ = setup(monkeypatch, handler)
    client.start()
    try:
        send(client, b"bad")
        send(client, b"good")
        assert finished.wait(2)
        assert client.callback_error_count == 1
        assert client.last_callback_error is failure
        assert client.last_error == "MQTT on_message callback failed"
    finally:
        client.close()


def test_slow_callback_has_bounded_queue_and_never_blocks_producer(monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    received = []

    def handler(message):
        received.append(message.payload)
        if message.payload == b"one":
            entered.set()
            assert release.wait(2)
        else:
            finished.set()

    client, _ = setup(monkeypatch, handler, notification_capacity=1)
    client.start()
    try:
        send(client, b"one")
        assert entered.wait(2)
        send(client, b"two")
        send(client, b"three")
        assert client.dropped_count == 1
        release.set()
        assert finished.wait(2)
        assert received == [b"one", b"two"]
    finally:
        release.set()
        client.close()


def test_close_timeout_is_visible_and_can_be_retried(monkeypatch):
    entered, release = Event(), Event()

    def handler(message):
        entered.set()
        release.wait(2)

    client, _ = setup(monkeypatch, handler, timeout=0.01)
    client.start()
    worker = client._callback_worker
    try:
        send(client, b"one")
        assert entered.wait(2)
        with pytest.raises(ConnectionError, match="callback"):
            client.close()
        assert worker.is_alive()
        assert client.connection_status == "CLOSED"
    finally:
        release.set()
        worker.join(2)
        client.close()
    assert client._callback_worker is None


def test_callback_cannot_close_its_own_worker(monkeypatch):
    finished = Event()
    failures = []

    def handler(message):
        try:
            client.close()
        except OperationError as error:
            failures.append(error)
        finished.set()

    client, _ = setup(monkeypatch, handler)
    client.start()
    try:
        send(client, b"one")
        assert finished.wait(2)
        assert "owner" in str(failures[0])
        assert not client._closed
    finally:
        client.close()


def test_callback_can_publish_without_blocking_network_processing(monkeypatch):
    finished = Event()

    def handler(message):
        client.publish("plant/ack", b"received", qos=1)
        finished.set()

    client, raw = setup(monkeypatch, handler)
    client.start()
    client._on_connect(raw, None, None, 0, None)
    try:
        send(client, b"one")
        assert finished.wait(2)
        assert client.callback_error_count == 0
    finally:
        client.close()


def test_invalid_callback_rejected_before_start():
    with pytest.raises(TypeError, match="callable"):
        MqttClient(MqttConfig(host="localhost"), on_message="handler")

    async def handler(message):
        pass

    with pytest.raises(TypeError, match="synchronous"):
        MqttClient(MqttConfig(host="localhost"), on_message=handler)


def test_idle_worker_is_woken_on_close(monkeypatch):
    client, _ = setup(monkeypatch, lambda message: None)
    client.start()
    worker = client._callback_worker
    client.close()
    assert not worker.is_alive()
