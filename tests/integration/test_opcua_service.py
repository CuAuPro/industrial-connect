import socket
import time

import pytest

from industrial_connect.opcua import OpcUaClient, OpcUaConfig, OpcUaWrite

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("service", ["opcua"], indirect=True)
def test_opcua_real_read_write_subscription_and_cleanup(service):
    sync = pytest.importorskip("asyncua.sync")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"opc.tcp://127.0.0.1:{port}/ic-test/"
    server = sync.Server()
    server.set_endpoint(endpoint)
    index = server.register_namespace("urn:industrial-connect:test")
    variable = server.nodes.objects.add_variable(index, "Reading", 1.0)
    slow_variable = server.nodes.objects.add_variable(index, "SlowReading", 8.0)
    variable.set_writable()
    server.start()
    client = OpcUaClient(
        OpcUaConfig(endpoint=endpoint, security_policy="None", reconnect_interval=0.1)
    )
    try:
        node_id = variable.nodeid.to_string()
        slow_id = slow_variable.nodeid.to_string()
        sub_id = client.subscribe(
            [node_id, slow_id],
            publishing_interval_ms=50,
            sampling_interval_ms={node_id: 25, slow_id: 100},
        )
        client.connect()
        assert client.read_many([node_id])[0].value == 1.0
        result = client.write_many([OpcUaWrite(node_id, 2.5, "Double")])
        assert result[0].status_code.is_good()
        assert variable.read_value() == 2.5
        observed = set()
        while True:
            event = client.get_notification(timeout=5)
            assert event.subscription_id == sub_id
            observed.update((value.node_id, value.value) for value in event.values)
            if (node_id, 2.5) in observed and (slow_id, 8.0) in observed:
                break
        client.unsubscribe(sub_id)
    finally:
        client.close()
        server.stop()
    assert not client._supervisor.is_alive()


@pytest.mark.parametrize("service", ["opcua"], indirect=True)
@pytest.mark.parametrize("policy", ["Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"])
def test_opcua_secure_pinned_certificate_handshake(service, tmp_path, policy):
    from asyncua import ua
    from asyncua.crypto import cert_gen
    from asyncua.sync import Server
    from cryptography.x509.oid import ExtendedKeyUsageOID

    from industrial_connect.opcua import generate_application_certificate

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"opc.tcp://127.0.0.1:{port}/secure/"
    server = Server()
    server.set_endpoint(endpoint)
    server_uri = "urn:industrial-connect:test:server"
    server.set_application_uri(server_uri)
    server_key, server_cert = tmp_path / "server-key.pem", tmp_path / "server-cert.der"
    server.tloop.post(
        cert_gen.setup_self_signed_certificate(
            server_key,
            server_cert,
            server_uri,
            "localhost",
            [ExtendedKeyUsageOID.SERVER_AUTH],
            {},
        )
    )
    server.load_certificate(server_cert)
    server.load_private_key(server_key)
    server.set_security_policy([getattr(ua.SecurityPolicyType, policy + "_SignAndEncrypt")])
    index = server.register_namespace("urn:industrial-connect:test")
    node = server.nodes.objects.add_variable(index, "SecureReading", 4.5)
    client_key, client_cert = tmp_path / "client-key.pem", tmp_path / "client-cert.der"
    client_uri = "urn:industrial-connect:test:client"
    generate_application_certificate(client_key, client_cert, application_uri=client_uri)
    server.start()
    try:
        with OpcUaClient(
            OpcUaConfig(
                endpoint=endpoint,
                security_policy=policy,
                application_uri=client_uri,
                certificate=str(client_cert),
                private_key=str(client_key),
                server_certificate=str(server_cert),
            )
        ) as client:
            assert client.read_many([node.nodeid.to_string()])[0].value == 4.5
    finally:
        server.stop()


@pytest.mark.parametrize("service", ["opcua"], indirect=True)
def test_opcua_real_disconnect_and_subscription_recovery(service):
    from asyncua import ua
    from asyncua.sync import Server

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"opc.tcp://127.0.0.1:{port}/reconnect/"

    def start_server(value):
        server = Server()
        server.set_endpoint(endpoint)
        index = server.register_namespace("urn:industrial-connect:test")
        node = server.nodes.objects.add_variable(ua.NodeId("Reading", index), "Reading", value)
        server.start()
        return server, node.nodeid.to_string()

    server, node_id = start_server(1.0)
    client = OpcUaClient(
        OpcUaConfig(endpoint=endpoint, security_policy="None", timeout=1, reconnect_interval=0.1)
    )
    try:
        client.subscribe([node_id], publishing_interval_ms=50)
        client.connect()
        assert client.get_notification(timeout=5).values[0].value == 1.0

        # asyncua's test server does not close external session tasks on stop.
        # Explicitly release them so the intentional outage leaves no pending tasks.
        async def close_external_sessions():
            for session in list(server.aio_obj.iserver._external_sessions.values()):
                await session.close_session()

        server.tloop.post(close_external_sessions())
        server.stop()
        server = None
        deadline = time.monotonic() + 10
        while True:
            event = client.get_notification(timeout=5)
            if event.kind == "disconnect":
                assert event.values[0].value is None
                break
            assert time.monotonic() < deadline
        server, _ = start_server(3.0)
        deadline = time.monotonic() + 10
        while True:
            event = client.get_notification(timeout=5)
            if event.kind == "data" and event.values[0].value == 3.0:
                break
            assert time.monotonic() < deadline
    finally:
        client.close()
        if server is not None:
            server.stop()
