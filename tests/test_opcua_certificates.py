from types import SimpleNamespace

import pytest

from industrial_connect.errors import OperationError
from industrial_connect.opcua import OpcUaClient, OpcUaConfig, generate_application_certificate


def test_certificate_generation_is_explicit_and_constructor_has_no_io(tmp_path):
    pytest.importorskip("asyncua")
    key, cert, server = (tmp_path / name for name in ("key.pem", "client.der", "server.der"))
    config = OpcUaConfig(
        endpoint="opc.tcp://localhost:4840",
        application_uri="urn:test:explicit",
        private_key=str(key),
        certificate=str(cert),
        server_certificate=str(server),
    )
    OpcUaClient(config).close()
    assert list(tmp_path.iterdir()) == []
    generate_application_certificate(key, cert, application_uri=config.application_uri)
    original_key, original_cert = key.read_bytes(), cert.read_bytes()
    generate_application_certificate(key, cert, application_uri=config.application_uri)
    assert (key.read_bytes(), cert.read_bytes()) == (original_key, original_cert)
    generate_application_certificate(key, cert, application_uri=config.application_uri, force=True)
    assert key.read_bytes() == original_key and cert.read_bytes() != original_cert
    assert not server.exists()


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
