from types import SimpleNamespace

import pytest

from industrial_connect.errors import ConnectionError, OperationError
from industrial_connect.mqtt import MqttClient, MqttConfig


def test_omitted_client_ids_are_unique_and_explicit_ids_are_preserved():
    first = MqttConfig(host="localhost")
    second = MqttConfig(host="localhost")
    assert first.client_id != second.client_id
    assert len(first.client_id) == 23 and first.client_id.isalnum()
    assert MqttConfig(host="localhost", client_id="collector-1").client_id == "collector-1"
    with pytest.raises(ValueError, match="client_id"):
        MqttConfig(host="localhost", client_id="")


def test_generated_client_id_is_reused_on_reconnect():
    pytest.importorskip("paho.mqtt.client")
    config = MqttConfig(host="localhost")
    client = MqttClient(config)
    first = client._make_client()
    replacement = client._make_client()
    assert first._client_id == replacement._client_id == config.client_id.encode()
    client.close()


def test_client_id_environment_override(monkeypatch):
    monkeypatch.setenv("ID_TEST_HOST", "localhost")
    monkeypatch.delenv("ID_TEST_CLIENT_ID", raising=False)
    first = MqttConfig.from_env(prefix="ID_TEST_")
    second = MqttConfig.from_env(prefix="ID_TEST_")
    assert first.client_id != second.client_id
    monkeypatch.setenv("ID_TEST_CLIENT_ID", "environment-id")
    assert MqttConfig.from_env(prefix="ID_TEST_").client_id == "environment-id"
    assert (
        MqttConfig.from_env(prefix="ID_TEST_", client_id="explicit-id").client_id == "explicit-id"
    )


class FakeMqtt:
    def __init__(self):
        self.subscriptions = []
        self.unsubscribed = []
        self.disconnected = self.stopped = False
        self.info = SimpleNamespace(
            rc=0, wait_for_publish=lambda **_: None, is_published=lambda: True
        )

    def connect_async(self, *args):
        self.address = args

    def loop_start(self):
        return 0

    def subscribe(self, topic, qos):
        self.subscriptions.append((topic, qos))
        return 0, 1

    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)
        return 0, 1

    def publish(self, *args, **kwargs):
        return self.info

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        self.stopped = True


def make_client(monkeypatch, **kwargs):
    client = MqttClient(
        MqttConfig(host="localhost", client_id="test", tls=False, timeout=0.05, **kwargs)
    )
    fake = FakeMqtt()
    monkeypatch.setattr(client, "_make_client", lambda: fake)
    return client, fake


def test_mqtt_ack_reconnect_resubscribe_and_close(monkeypatch):
    client, fake = make_client(monkeypatch)
    client.subscribe("plant/#", qos=1)
    client.start()
    client._on_connect(fake, None, None, 0, None)
    client.connect()
    assert fake.subscriptions == [("plant/#", 1)]
    assert client.publish("plant/set", b"value", qos=1, wait=True) is fake.info
    client._on_disconnect(fake, None, None, 1, None)
    assert not client.connected
    client._on_connect(fake, None, None, 0, None)
    assert fake.subscriptions == [("plant/#", 1)] * 2
    client.unsubscribe("plant/#")
    assert fake.unsubscribed == ["plant/#"]
    client.close()
    client.close()
    assert fake.disconnected and fake.stopped
    assert client.connection_status == "CLOSED"
    with pytest.raises(ConnectionError):
        client.publish("plant/set", b"value")


def test_mqtt_overflow_preserves_metadata(monkeypatch):
    client, _ = make_client(monkeypatch, notification_capacity=1)
    message = SimpleNamespace(topic="plant/data", payload=b"raw", qos=2, retain=True)
    client._on_message(None, None, message)
    client._on_message(None, None, message)
    assert client.dropped_count == 1
    event = client.get_notification(timeout=0.01)
    assert (event.topic, event.payload, event.qos, event.retain) == ("plant/data", b"raw", 2, True)


def test_mqtt_unconfirmed_publish_and_connect_timeout(monkeypatch):
    client, fake = make_client(monkeypatch)
    with pytest.raises(ConnectionError):
        client.connect()
    client._on_connect(fake, None, None, 0, None)
    fake.info.is_published = lambda: False
    with pytest.raises(OperationError, match="uncertain") as error:
        client.publish("plant/set", b"x", wait=True)
    assert isinstance(error.value.__cause__, TimeoutError)
    fake.info.rc = 4
    with pytest.raises(OperationError, match="failed"):
        client.publish("plant/set", b"x", wait=False)
    client.close()


def test_mqtt_tls_uses_verified_context(monkeypatch):
    import industrial_connect.mqtt as module

    options = {}
    fake = SimpleNamespace(
        username_pw_set=lambda *a: options.update(auth=a),
        tls_set_context=lambda context: options.update(tls=context),
        reconnect_delay_set=lambda *a: options.update(reconnect=a),
        max_inflight_messages_set=lambda n: options.update(inflight=n),
        max_queued_messages_set=lambda n: options.update(queued=n),
    )
    driver = SimpleNamespace(
        Client=lambda *a, **k: fake, CallbackAPIVersion=SimpleNamespace(VERSION2=2), MQTTv311=4
    )
    monkeypatch.setattr(module, "require", lambda *_: driver)
    client = MqttClient(MqttConfig(host="localhost", client_id="id", username="u", password="s"))
    client._make_client()
    assert options["tls"].check_hostname
    assert options["tls"].verify_mode == module.ssl.CERT_REQUIRED
    assert options["auth"] == ("u", "s")
    assert options["reconnect"] == (1, 30)
    assert options["inflight"] == 1000
    assert options["queued"] == 20000


def test_real_paho_configuration_without_network():
    pytest.importorskip("paho.mqtt.client")
    client = MqttClient(MqttConfig(host="localhost", client_id="test"))
    raw = client._make_client()
    assert raw._ssl_context.check_hostname
    assert raw._connect_timeout == 10
    assert raw._max_inflight_messages == 1000
    assert raw._max_queued_messages == 20000
    client.close()


@pytest.mark.parametrize("topic", ["plant/+bad", "plant/#/bad", "plant/bad#", ""])
def test_bad_subscription_filters_are_rejected_by_paho(topic):
    pytest.importorskip("paho.mqtt.client")
    client = MqttClient(MqttConfig(host="localhost", client_id="test"))
    client._client = client._make_client()
    client.connected = True
    with pytest.raises(OperationError) as error:
        client.subscribe(topic)
    assert isinstance(error.value.__cause__, ValueError)
    assert topic not in client._subscriptions
    client.close()


def test_default_publish_never_waits_or_polls_completion(monkeypatch):
    client, fake = make_client(monkeypatch)
    client.start()
    client._on_connect(fake, None, None, 0, None)

    def unexpected_wait(**kwargs):
        pytest.fail("Nonblocking publish must not wait or poll for completion")

    fake.info.wait_for_publish = unexpected_wait
    fake.info.is_published = unexpected_wait
    try:
        for _ in range(1000):
            assert client.publish("plant/set", b"value", qos=1) is fake.info
    finally:
        client.close()


@pytest.mark.parametrize("inflight, queued", [(128, 4096), (0, 0)])
def test_paho_message_limits_are_configurable(inflight, queued):
    pytest.importorskip("paho.mqtt.client")
    config = MqttConfig(
        host="localhost", max_inflight_messages=inflight, max_queued_messages=queued
    )
    client = MqttClient(config)
    raw = client._make_client()
    assert raw._max_inflight_messages == inflight
    assert raw._max_queued_messages == queued
    client.close()


@pytest.mark.parametrize("field", ["max_inflight_messages", "max_queued_messages"])
def test_negative_message_limits_are_rejected(field):
    with pytest.raises(ValueError, match="nonnegative"):
        MqttConfig(host="localhost", **{field: -1})


def test_mqtt_subscription_rejection_and_callback_error_are_visible(monkeypatch):
    client, fake = make_client(monkeypatch)
    client.subscribe("plant/#")

    def fail(*a, **k):
        raise ValueError("subscribe failure")

    fake.subscribe = fail
    client._on_connect(fake, None, None, 0, None)
    assert client.last_error == "MQTT subscription request failed"
    client._on_subscribe(fake, None, 1, [SimpleNamespace(is_failure=True)], None)
    assert client.last_error == "MQTT broker rejected a subscription"


def test_mqtt_tls_file_failure_is_chained(monkeypatch):
    client, fake = make_client(monkeypatch)

    def fail():
        raise FileNotFoundError("missing certificate")

    monkeypatch.setattr(client, "_make_client", fail)
    with pytest.raises(ConnectionError) as error:
        client.start()
    assert isinstance(error.value.__cause__, FileNotFoundError)
