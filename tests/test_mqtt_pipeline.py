"""Exercise real Paho pipelining against a minimal local MQTT protocol peer."""

import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from industrial_connect.mqtt import MqttClient, MqttConfig


def test_real_paho_sends_a_batch_before_any_acknowledgement():
    pytest.importorskip("paho.mqtt.client")
    count = 32
    received = Event()
    release = Event()

    def peer(listener):
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(5)
            with connection.makefile("rb") as stream:

                def packet():
                    header = stream.read(1)
                    assert header
                    length, shift = 0, 0
                    while True:
                        digit = stream.read(1)[0]
                        length += (digit & 127) << shift
                        if not digit & 128:
                            break
                        shift += 7
                    body = stream.read(length)
                    assert len(body) == length
                    return header[0], body

                assert packet()[0] == 0x10  # CONNECT
                connection.sendall(b"\x20\x02\x00\x00")
                ids = []
                for _ in range(count):
                    header, body = packet()
                    assert header == 0x32  # QoS 1 PUBLISH
                    topic_length = int.from_bytes(body[:2], "big")
                    ids.append(body[2 + topic_length : 4 + topic_length])
                received.set()
                assert release.wait(5)
                connection.sendall(b"".join(b"\x40\x02" + mid for mid in ids))
                assert packet()[0] == 0xE0  # DISCONNECT

    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as executor:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        server = executor.submit(peer, listener)
        config = MqttConfig(
            host="127.0.0.1",
            port=listener.getsockname()[1],
            tls=False,
            timeout=5,
            max_inflight_messages=count,
            max_queued_messages=count,
        )
        try:
            with MqttClient(config) as client:
                pending = [client.publish("plant/data", b"reading", qos=1) for _ in range(count)]
                assert received.wait(5), "Publishes were serialized behind broker acknowledgements"
                assert all(not info.is_published() for info in pending)
                release.set()
                for info in pending:
                    info.wait_for_publish(timeout=5)
                    assert info.is_published()
        finally:
            release.set()
        server.result(timeout=5)
