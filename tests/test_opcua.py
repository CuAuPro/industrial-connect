from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from industrial_connect.errors import OperationError
from industrial_connect.opcua import OpcUaClient, OpcUaConfig, OpcUaWrite, _Handler

ua = pytest.importorskip("asyncua.ua")


class FakeSubscription:
    def __init__(self):
        self.deleted = False
        self.groups = []

    def subscribe_data_change(self, nodes, **kwargs):
        self.nodes = nodes
        self.options = kwargs
        self.groups.append((list(nodes), kwargs))
        return [
            1 if node != "bad" else ua.StatusCode(ua.StatusCodes.BadNodeIdUnknown) for node in nodes
        ]

    def delete(self):
        self.deleted = True


class FakeOpcUa:
    def __init__(self):
        self.subscriptions = []
        self.disconnected = False
        self.read_failure = False

    def connect(self):
        pass

    def check_connection(self):
        pass

    def get_node(self, node):
        return node

    def create_subscription(self, interval, handler):
        subscription = FakeSubscription()
        subscription.interval = interval
        self.subscriptions.append(subscription)
        return subscription

    def read_attributes(self, nodes, attribute):
        if self.read_failure:
            raise RuntimeError("connection lost")
        timestamp = datetime.now(UTC)
        return [
            ua.DataValue(
                ua.Variant(1.5),
                StatusCode=ua.StatusCode(ua.StatusCodes.BadNodeIdUnknown if node == "bad" else 0),
                SourceTimestamp=timestamp,
                ServerTimestamp=timestamp,
            )
            for node in nodes
        ]

    def write_values(self, nodes, values, raise_on_partial_error):
        assert raise_on_partial_error is False
        assert values[0].VariantType == ua.VariantType.Double
        return [ua.StatusCode(0), ua.StatusCode(ua.StatusCodes.BadNotWritable)]

    def disconnect(self):
        self.disconnected = True


def make_client(monkeypatch, **kwargs):
    client = OpcUaClient(
        OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy="None", **kwargs)
    )
    fake = FakeOpcUa()
    monkeypatch.setattr(client, "_make_client", lambda: fake)
    # Exercise lifecycle deterministically; a separate test covers the real supervisor.
    monkeypatch.setattr(client, "_start_supervisor", lambda: None)
    return client, fake


def test_batch_reads_partial_status_timestamps_and_typed_writes(monkeypatch):
    client, fake = make_client(monkeypatch)
    values = client.read_many(["good", "bad"])
    assert values[0].status_code.is_good()
    assert values[0].source_timestamp.tzinfo is not None
    assert values[1].status_code.is_bad()
    assert values[1].value is None
    results = client.write_many([OpcUaWrite("good", 2.5, "Double"), OpcUaWrite("bad", 2, "Int16")])
    assert results[0].status_code.is_good()
    assert results[1].status_code.is_bad()
    with pytest.raises(ValueError, match="variant"):
        client.write_many([OpcUaWrite("good", 2.5, "Invalid")])
    fake.read_failure = True
    with pytest.raises(OperationError) as error:
        client.read_many(["good"])
    assert isinstance(error.value.__cause__, RuntimeError)
    assert not client.connected
    client.close()


def test_subscription_failure_disconnect_restore_unsubscribe(monkeypatch):
    client, fake = make_client(monkeypatch)
    subscription_id = client.subscribe(["good", "bad"], sampling_interval_ms=25)
    client.connect()
    event = client.get_notification(timeout=0.1)
    assert event.kind == "subscription_failed"
    assert event.values[0].node_id == "bad"
    with client._lock:
        client._disconnect_locked(invalidate=True)
    event = client.get_notification(timeout=0.1)
    assert event.kind == "disconnect"
    assert all(value.value is None and value.status_code.is_bad() for value in event.values)
    client.connect()
    assert len(fake.subscriptions) == 2
    assert fake.subscriptions[-1].options["sampling_interval"] == 25
    client.unsubscribe(subscription_id)
    assert fake.subscriptions[-1].deleted
    assert not client._subscriptions
    client.close()
    client.close()


def test_subscription_callback_metadata_and_overflow(monkeypatch):
    client, _ = make_client(monkeypatch, notification_capacity=1)
    handler = _Handler(client, "subscription")
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    dv = ua.DataValue(ua.Variant(7), SourceTimestamp=timestamp, ServerTimestamp=timestamp)
    data = SimpleNamespace(monitored_item=SimpleNamespace(Value=dv))
    node = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "node"))
    handler.datachange_notification(node, 7, data)
    handler.datachange_notification(node, 7, data)
    assert client.dropped_count == 1
    event = client.get_notification(timeout=0.1)
    assert event.values[0].value == 7
    assert event.subscription_id == "subscription"
    assert event.values[0].source_timestamp == timestamp
    assert event.values[0].server_timestamp == timestamp
    handler.status_change_notification(ua.StatusCode(ua.StatusCodes.BadTimeout))
    assert client._subscription_fault.is_set()


def test_mixed_sampling_restores_groups_and_invalidates_only_subscribed_nodes(monkeypatch):
    client, fake = make_client(monkeypatch)
    intervals = {"fast": 25, "bad": 500, "slow": 500, "fast2": 25}
    nodes = list(intervals)
    subscription_id = client.subscribe(
        nodes, publishing_interval_ms=200, sampling_interval_ms=intervals, queue_size=7
    )
    intervals["fast"] = 900
    client.connect()
    expected = [
        (["fast", "fast2"], {"sampling_interval": 25, "queuesize": 7}),
        (["bad", "slow"], {"sampling_interval": 500, "queuesize": 7}),
    ]
    assert len(fake.subscriptions) == 1
    assert fake.subscriptions[0].interval == 200
    assert fake.subscriptions[0].groups == expected
    event = client.get_notification(timeout=0.1)
    assert event.kind == "subscription_failed" and event.subscription_id == subscription_id
    assert [value.node_id for value in event.values] == ["bad"]
    assert event.values[0].status_code.value == ua.StatusCodes.BadNodeIdUnknown
    client.read_many(["read_only"])
    client._disconnect_locked(invalidate=True)
    event = client.get_notification(timeout=0.1)
    assert event.kind == "disconnect" and event.subscription_id == subscription_id
    assert [value.node_id for value in event.values] == nodes
    assert all(value.status_code.value == ua.StatusCodes.BadNotConnected for value in event.values)
    client.connect()
    assert len(fake.subscriptions) == 2
    assert fake.subscriptions[-1].groups == expected
    assert fake.subscriptions[-1].interval == 200
    client.unsubscribe(subscription_id)
    assert fake.subscriptions[-1].deleted
    client.close()


def test_mixed_sampling_aggregates_failures_in_requested_order(monkeypatch):
    client, fake = make_client(monkeypatch)
    status = ua.StatusCode(ua.StatusCodes.BadNodeIdUnknown)
    original_create = fake.create_subscription

    def create(interval, handler):
        remote = original_create(interval, handler)
        remote.subscribe_data_change = lambda nodes, **kwargs: [status for _ in nodes]
        return remote

    monkeypatch.setattr(fake, "create_subscription", create)
    nodes = ["a", "b", "c"]
    subscription_id = client.subscribe(nodes, sampling_interval_ms={"a": 10, "b": 20, "c": 10})
    client.connect()
    event = client.get_notification(timeout=0.1)
    assert event.subscription_id == subscription_id and event.kind == "subscription_failed"
    assert [value.node_id for value in event.values] == nodes
    assert all(value.status_code is status for value in event.values)
    assert client.notifications.empty()
    client.close()


@pytest.mark.parametrize(
    "sampling",
    [
        {},
        {"a": 10},
        {"a": 10, "b": 20, "unknown": 30},
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        {"a": -1, "b": 20},
        {"a": float("nan"), "b": 20},
        {"a": 10, "b": float("inf")},
    ],
)
def test_invalid_sampling_fails_before_network(monkeypatch, sampling):
    client, fake = make_client(monkeypatch)
    with pytest.raises(ValueError):
        client.subscribe(["a", "b"], sampling_interval_ms=sampling)
    assert not client._subscriptions and not fake.subscriptions
    assert client._client is None
    client.close()


def test_mixed_sampling_group_failure_cleans_remote_subscription(monkeypatch):
    client, fake = make_client(monkeypatch)
    client.connect()
    original_create = fake.create_subscription

    def create(interval, handler):
        remote = original_create(interval, handler)

        def subscribe(nodes, **kwargs):
            if nodes == ["b"]:
                raise RuntimeError("second group failed")
            return [1]

        remote.subscribe_data_change = subscribe
        return remote

    monkeypatch.setattr(fake, "create_subscription", create)
    with pytest.raises(OperationError):
        client.subscribe(["a", "b"], sampling_interval_ms={"a": 10, "b": 20})
    assert fake.subscriptions[-1].deleted
    assert not client._subscriptions and not client._remote_subscriptions
    client.close()


def test_supervisor_shutdown(monkeypatch):
    from threading import Event

    ready = Event()
    fake = FakeOpcUa()
    fake.connect = ready.set
    client = OpcUaClient(
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840", security_policy="None", reconnect_interval=0.01
        )
    )
    monkeypatch.setattr(client, "_make_client", lambda: fake)
    client.start()
    assert ready.wait(1)
    client.close()
    assert not client._supervisor.is_alive()
    assert fake.disconnected


def test_certificate_generation_is_explicit_and_identity_matches(tmp_path):
    from cryptography import x509

    from industrial_connect.opcua import generate_application_certificate

    key, cert = tmp_path / "key.pem", tmp_path / "cert.der"
    assert not key.exists()
    generated_cert, generated_key = generate_application_certificate(
        key,
        cert,
        application_uri="urn:test:client",
        host_name="localhost",
    )
    assert (generated_cert, generated_key) == (cert, key)
    parsed = x509.load_der_x509_certificate(cert.read_bytes())
    san = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "urn:test:client" in san.get_values_for_type(x509.UniformResourceIdentifier)


@pytest.mark.parametrize("policy", ["Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"])
def test_secure_client_requires_pinned_cert_and_validates_identity(tmp_path, policy):
    from industrial_connect.opcua import generate_application_certificate

    with pytest.raises(ValueError, match="pinned"):
        OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy=policy)
    key, cert = tmp_path / "key.pem", tmp_path / "cert.der"
    generate_application_certificate(
        key, cert, application_uri="urn:test:secure", host_name="localhost"
    )
    client = OpcUaClient(
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            security_policy=policy,
            application_uri="urn:test:secure",
            certificate=str(cert),
            private_key=str(key),
            server_certificate=str(cert),
            auto_generate_certificate=False,
        )
    )
    raw = client._make_client()
    try:
        assert raw.aio_obj.certificate_validator is not None
        assert raw.application_uri == "urn:test:secure"
        assert raw.aio_obj.security_policy.Mode == ua.MessageSecurityMode.SignAndEncrypt
        from asyncua.crypto import security_policies

        assert isinstance(
            raw.aio_obj.security_policy, getattr(security_policies, "SecurityPolicy" + policy)
        )
    finally:
        raw.tloop.stop()


@pytest.mark.parametrize("policy", ["Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"])
def test_pinned_certificate_validator_rejects_another_certificate(tmp_path, policy):
    import asyncio

    from cryptography import x509

    from industrial_connect.opcua import generate_application_certificate

    uri = "urn:test:pinned"
    key, cert = tmp_path / "key.pem", tmp_path / "cert.der"
    other_key, other_cert = tmp_path / "other-key.pem", tmp_path / "other-cert.der"
    generate_application_certificate(key, cert, application_uri=uri)
    generate_application_certificate(other_key, other_cert, application_uri=uri)
    client = OpcUaClient(
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            security_policy=policy,
            application_uri=uri,
            certificate=str(cert),
            private_key=str(key),
            server_certificate=str(cert),
            auto_generate_certificate=False,
        )
    )
    raw = client._make_client()
    try:
        foreign = x509.load_der_x509_certificate(other_cert.read_bytes())
        with pytest.raises(ua.UaStatusCodeError) as error:
            asyncio.run(raw.aio_obj.certificate_validator(foreign, None))
        assert error.value.code == ua.StatusCodes.BadCertificateUntrusted
    finally:
        raw.tloop.stop()
