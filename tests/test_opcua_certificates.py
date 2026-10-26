from dataclasses import replace
from types import SimpleNamespace

import pytest

from industrial_connect.errors import OperationError
from industrial_connect.opcua import (
    OpcUaClient,
    OpcUaConfig,
    generate_application_certificate,
)


def test_constructor_generates_reuses_and_force_renews_without_connecting(tmp_path):
    pytest.importorskip("asyncua")
    key, cert, server = (
        tmp_path / "client-key.pem",
        tmp_path / "client.der",
        tmp_path / "server.der",
    )
    server.write_bytes(b"server certificate must not be overwritten")
    config = OpcUaConfig(
        endpoint="opc.tcp://localhost:4840",
        application_uri="urn:test:init",
        private_key=str(key),
        certificate=str(cert),
        server_certificate=str(server),
    )
    client = OpcUaClient(config)
    assert config.auto_generate_certificate is True
    assert client._client is None and client._supervisor is None
    assert not client.connected
    client.close()
    original_key, original_cert = key.read_bytes(), cert.read_bytes()
    OpcUaClient(config).close()
    assert key.read_bytes() == original_key
    assert cert.read_bytes() == original_cert
    OpcUaClient(
        replace(config, auto_generate_certificate=False, force_certificate_regeneration=True)
    ).close()
    assert key.read_bytes() == original_key
    assert cert.read_bytes() != original_cert
    assert server.read_bytes() == b"server certificate must not be overwritten"


def test_generation_can_be_disabled(tmp_path):
    client = OpcUaClient(
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            private_key=str(tmp_path / "key.pem"),
            certificate=str(tmp_path / "client.der"),
            server_certificate=str(tmp_path / "server.der"),
            auto_generate_certificate=False,
        )
    )
    assert list(tmp_path.iterdir()) == []
    client.close()


def test_unsecured_constructor_skips_default_generation(tmp_path, monkeypatch):
    import industrial_connect.opcua as module

    def unexpected(*args, **kwargs):
        pytest.fail("Unsecured connections must not generate certificates")

    monkeypatch.setattr(module, "generate_application_certificate", unexpected)
    config = OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy="None")
    assert config.auto_generate_certificate is True
    OpcUaClient(config).close()
    assert list(tmp_path.iterdir()) == []


def test_forced_generation_failure_preserves_existing_certificate_and_stops_thread(
    tmp_path, monkeypatch
):
    import industrial_connect.opcua as module

    key, cert = tmp_path / "key.pem", tmp_path / "client.der"
    key.write_bytes(b"existing key")
    cert.write_bytes(b"existing certificate")
    stopped = []

    def fail(key_file, certificate_file, **kwargs):
        certificate_file.write_bytes(b"incomplete replacement")
        raise OSError("generation failed")

    fake = SimpleNamespace(
        setup_self_signed_certificate=fail, tloop=SimpleNamespace(stop=lambda: stopped.append(True))
    )
    monkeypatch.setattr(module, "require", lambda *_: SimpleNamespace(Client=lambda *_: fake))
    with pytest.raises(OperationError) as error:
        generate_application_certificate(key, cert, application_uri="urn:test:init", force=True)
    assert isinstance(error.value.__cause__, OSError)
    assert cert.read_bytes() == b"existing certificate"
    assert key.read_bytes() == b"existing key"
    assert stopped == [True]
    assert set(tmp_path.iterdir()) == {key, cert}


@pytest.mark.parametrize(
    "options",
    [
        {"security_policy": "None", "force_certificate_regeneration": True},
        {"private_key_password": "secret"},
        {"server_certificate": "client.der"},
        {"private_key": "client.der"},
    ],
)
def test_invalid_automatic_generation_configuration(options):
    config = dict(
        endpoint="opc.tcp://localhost:4840",
        certificate="client.der",
        private_key="key.pem",
        server_certificate="server.der",
        auto_generate_certificate=True,
    )
    with pytest.raises(ValueError):
        OpcUaConfig(**(config | options))
