from types import SimpleNamespace

import pytest

from industrial_connect.errors import OperationError
from industrial_connect.opcua import OpcUaClient, OpcUaConfig, generate_application_certificate


def test_certificate_helper_and_constructor_has_no_io(tmp_path):
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


@pytest.mark.parametrize("name", ["default", "datacol1"])
def test_automatic_relative_paths_reuse_and_force_once_per_client(tmp_path, monkeypatch, name):
    from dataclasses import replace
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    config = OpcUaConfig(
        endpoint="opc.tcp://localhost:4840", name=name, server_certificate="trusted-server.der"
    )
    client = OpcUaClient(config)
    assert list(tmp_path.iterdir()) == []
    assert config.certificate == f"data/opcua/{name}/client-certificate.der"
    assert config.private_key == f"data/opcua/{name}/client-private-key.pem"
    client._prepare_certificate()
    key, cert = Path(config.private_key), Path(config.certificate)
    original = key.read_bytes(), cert.read_bytes()
    client.close()
    reused = OpcUaClient(config)
    reused._prepare_certificate()
    assert (key.read_bytes(), cert.read_bytes()) == original
    reused.close()
    forced = OpcUaClient(replace(config, force_certificate_regeneration=True))
    forced._prepare_certificate()
    renewed = cert.read_bytes()
    assert key.read_bytes() == original[0] and renewed != original[1]
    forced._prepare_certificate()
    assert cert.read_bytes() == renewed
    forced.close()
    assert not (tmp_path / "trusted-server.der").exists()


def test_default_name_explicit_paths_and_partial_overrides():
    defaults = OpcUaConfig(endpoint="opc.tcp://localhost:4840", server_certificate="server.der")
    assert defaults.name == "default"
    assert defaults.certificate == "data/opcua/default/client-certificate.der"
    legacy = OpcUaConfig(
        endpoint=defaults.endpoint,
        name="datacol1",
        server_certificate="server.der",
        certificate="data/opcua/client-certificate.der",
        private_key="data/opcua/client-private-key.pem",
    )
    assert legacy.certificate == "data/opcua/client-certificate.der"
    assert legacy.private_key == "data/opcua/client-private-key.pem"
    partial = OpcUaConfig(
        endpoint=defaults.endpoint,
        name="datacol1",
        server_certificate="server.der",
        certificate="custom/client.der",
    )
    assert partial.certificate == "custom/client.der"
    assert partial.private_key == "data/opcua/datacol1/client-private-key.pem"


def test_connection_name_from_environment_and_mapping(monkeypatch):
    monkeypatch.setenv("NAMED_OPCUA_NAME", "datacol1")
    config = OpcUaConfig.from_env(
        prefix="NAMED_OPCUA_",
        endpoint="opc.tcp://localhost:4840",
        server_certificate="server.der",
    )
    assert config.name == "datacol1"
    assert config.certificate == "data/opcua/datacol1/client-certificate.der"
    mapped = OpcUaConfig.from_mapping(
        {"endpoint": config.endpoint, "name": "datacol2", "server_certificate": "server.der"},
        prefix="UNUSED_TEST_OPCUA_",
    )
    assert mapped.private_key == "data/opcua/datacol2/client-private-key.pem"


@pytest.mark.parametrize("name", [None, "", "../outside", "a\\b"])
def test_connection_name_rejects_invalid_path_components(name):
    with pytest.raises(ValueError, match="OPC UA name"):
        OpcUaConfig(endpoint="opc.tcp://localhost:4840", name=name, security_policy="None")


def test_managed_and_unsecured_clients_skip_generation_and_protect_server_pin(
    tmp_path, monkeypatch
):
    import industrial_connect.opcua as module

    monkeypatch.setattr(
        module,
        "generate_application_certificate",
        lambda *a, **kw: pytest.fail("Unexpected generation"),
    )
    for config in (
        OpcUaConfig(endpoint="opc.tcp://localhost:4840", security_policy="None"),
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            server_certificate="server.der",
            auto_generate_certificate=False,
            private_key_password="managed-key",
        ),
    ):
        client = OpcUaClient(config)
        client._prepare_certificate()
        client.close()
    with pytest.raises(ValueError, match="unencrypted"):
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            server_certificate="server.der",
            private_key_password="secret",
        )
    client = OpcUaClient(
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            server_certificate="same.der",
            certificate="same.der",
        )
    )
    with pytest.raises(ValueError, match="distinct paths"):
        client._prepare_certificate()
    client.close()


def test_force_flag_rejects_truthy_strings():
    with pytest.raises(ValueError, match="booleans"):
        OpcUaConfig(
            endpoint="opc.tcp://localhost:4840",
            server_certificate="server.der",
            force_certificate_regeneration="false",
        )
