from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ._notifications import Notifications
from ._optional import require
from .config import EnvConfig
from .errors import ConnectionError, DependencyError, OperationError, _cleanup
from .logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class OpcUaConfig(EnvConfig):
    _env_prefix = "OPCUA_"

    endpoint: str
    application_uri: str = "urn:industrial-connect:client"
    application_name: str = "industrial-connect"
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    timeout: float = 10.0
    reconnect_interval: float = 2.0
    notification_capacity: int = 1000
    security_policy: str = "Basic256Sha256"
    security_mode: str = "SignAndEncrypt"
    certificate: str | None = None
    private_key: str | None = None
    private_key_password: str | None = field(default=None, repr=False)
    server_certificate: str | None = None
    auto_generate_certificate: bool = True
    force_certificate_regeneration: bool = False

    def __post_init__(self):
        self._validate_numbers()
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme != "opc.tcp" or not endpoint.hostname or endpoint.username:
            raise ValueError("An opc.tcp endpoint without embedded credentials is required")
        if not self.application_uri or not self.application_name:
            raise ValueError("Application identity is required")
        if min(self.timeout, self.reconnect_interval, self.notification_capacity) <= 0:
            raise ValueError("Timeout, reconnect interval and capacity must be positive")
        if self.security_policy not in {
            "None",
            "Basic256Sha256",
            "Aes128Sha256RsaOaep",
            "Aes256Sha256RsaPss",
        }:
            raise ValueError("Unsupported OPC UA security policy")
        if self.security_mode not in {"Sign", "SignAndEncrypt"}:
            raise ValueError("Invalid OPC UA security mode")
        if self.security_policy != "None" and not all(
            (self.certificate, self.private_key, self.server_certificate)
        ):
            raise ValueError(
                "Secure OPC UA requires certificate, private_key and pinned server_certificate"
            )
        if self.security_policy == "None" and self.force_certificate_regeneration:
            raise ValueError("Certificate generation requires a secure OPC UA policy")
        if self.security_policy != "None" and (
            self.auto_generate_certificate or self.force_certificate_regeneration
        ):
            assert self.certificate and self.private_key and self.server_certificate
            if self.private_key_password:
                raise ValueError(
                    "Automatic certificate generation requires an unencrypted private key"
                )
            paths = [
                Path(path).resolve()
                for path in (self.certificate, self.private_key, self.server_certificate)
            ]
            if len(set(paths)) != 3:
                raise ValueError(
                    "Client certificate, private key and server certificate need distinct paths"
                )


@dataclass(frozen=True)
class OpcUaValue:
    node_id: str
    value: Any
    status_code: Any
    source_timestamp: datetime | None = None
    server_timestamp: datetime | None = None


@dataclass(frozen=True)
class OpcUaWrite:
    node_id: str
    value: Any
    variant_type: str


@dataclass(frozen=True)
class OpcUaWriteResult:
    node_id: str
    status_code: Any


@dataclass(frozen=True)
class OpcUaEvent:
    kind: str
    subscription_id: str
    values: tuple[OpcUaValue, ...]


def generate_application_certificate(
    key_file: str | Path,
    certificate_file: str | Path,
    *,
    application_uri: str,
    host_name: str | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Prepare a local certificate; force renews it while retaining an existing private key."""
    if not application_uri:
        raise ValueError("application_uri is required")
    key_file, certificate_file = Path(key_file), Path(certificate_file)
    if key_file.resolve() == certificate_file.resolve():
        raise ValueError("Certificate and private key need distinct paths")
    client = require("asyncua.sync", "opcua").Client("opc.tcp://localhost:4840")
    try:
        client.application_uri = application_uri
        if force:
            # Stage the replacement so generation failures preserve the existing certificate.
            certificate_file.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=certificate_file.parent) as directory:
                replacement = Path(directory) / "certificate.der"
                client.setup_self_signed_certificate(key_file, replacement, host_name=host_name)
                replacement.replace(certificate_file)
            return certificate_file, key_file
        return client.setup_self_signed_certificate(key_file, certificate_file, host_name=host_name)
    except Exception as exc:
        raise OperationError("Could not prepare OPC UA application certificate") from exc
    finally:
        # No session exists; stop the sync wrapper's event-loop thread explicitly.
        _cleanup(client.tloop.stop, "OPC UA certificate worker cleanup also failed")


class _Handler:
    def __init__(self, owner, subscription_id):
        self.owner = owner
        self.subscription_id = subscription_id

    def datachange_notification(self, node, value, data):
        dv = data.monitored_item.Value
        self.owner._emit(
            OpcUaEvent(
                "data",
                self.subscription_id,
                (
                    OpcUaValue(
                        node.nodeid.to_string(),
                        getattr(dv.Value, "Value", None) if dv.StatusCode.is_good() else None,
                        dv.StatusCode,
                        dv.SourceTimestamp,
                        dv.ServerTimestamp,
                    ),
                ),
            )
        )

    def status_change_notification(self, status):
        if getattr(status, "Status", status).is_bad():
            self.owner._subscription_fault.set()


class OpcUaClient(Notifications[OpcUaEvent]):
    """Sync operations and supervised subscriptions, independent of application models."""

    def __init__(self, config: OpcUaConfig):
        super().__init__(config.notification_capacity)
        self.config = config
        self.connected = False
        self.last_error: str | None = None
        self._client: Any = None
        self._closed = False
        self._lock = RLock()
        self._stop = Event()
        self._subscription_fault = Event()
        self._supervisor = None
        self._subscriptions = {}
        self._remote_subscriptions = {}
        if config.security_policy != "None" and (
            config.auto_generate_certificate or config.force_certificate_regeneration
        ):
            assert config.private_key and config.certificate
            generate_application_certificate(
                config.private_key,
                config.certificate,
                application_uri=config.application_uri,
                force=config.force_certificate_regeneration,
            )

    @property
    def connection_status(self) -> str:
        return "CLOSED" if self._closed else "CONNECTED" if self.connected else "DISCONNECTED"

    def _make_client(self):
        c = self.config
        client = require("asyncua.sync", "opcua").Client(
            c.endpoint,
            timeout=c.timeout,
            sync_wrapper_timeout=c.timeout + 1,
        )
        try:
            client.application_uri = c.application_uri
            client.aio_obj.name = c.application_name
            client.aio_obj.description = c.application_name
            if c.username:
                client.set_user(c.username)
            if c.password:
                client.set_password(c.password)
            if c.security_policy != "None":
                ua = require("asyncua.ua", "opcua")
                policies = require("asyncua.crypto.security_policies", "opcua")
                client.set_security(
                    getattr(policies, "SecurityPolicy" + c.security_policy),
                    certificate=c.certificate,
                    private_key=c.private_key,
                    private_key_password=c.private_key_password,
                    server_certificate=c.server_certificate,
                    mode=getattr(ua.MessageSecurityMode, c.security_mode),
                )
                self._set_validator(client)
            return client
        except BaseException:
            _cleanup(client.tloop.stop, "OPC UA client worker cleanup also failed")
            raise

    def _set_validator(self, client):
        assert self.config.server_certificate
        x509 = require("cryptography.x509", "opcua")
        hashes = require("cryptography.hazmat.primitives.hashes", "opcua")
        validators = require("asyncua.crypto.validator", "opcua")
        ua = require("asyncua.ua", "opcua")
        content = Path(self.config.server_certificate).read_bytes()
        pinned = (
            x509.load_pem_x509_certificate(content)
            if b"-----BEGIN" in content
            else x509.load_der_x509_certificate(content)
        )
        options = validators.CertificateValidatorOptions
        validator = validators.CertificateValidator(
            options.TIME_RANGE | options.URI | options.EXT_KEY_USAGE | options.PEER_SERVER,
        )

        async def validate(certificate, application):
            if certificate.fingerprint(hashes.SHA256()) != pinned.fingerprint(hashes.SHA256()):
                raise ua.UaStatusCodeError(ua.StatusCodes.BadCertificateUntrusted)
            await validator.validate(certificate, application)

        client.aio_obj.certificate_validator = validate

    def _check_open(self):
        if self._closed:
            raise ConnectionError("OPC UA client is closed")

    def _start_supervisor(self):
        if self._supervisor is None or not self._supervisor.is_alive():
            self._supervisor = Thread(
                target=self._supervise, name="industrial-connect-opcua", daemon=True
            )
            self._supervisor.start()

    def start(self):
        """Start connecting in the background; inspect connection_status for readiness."""
        with self._lock:
            self._check_open()
            self._start_supervisor()

    def connect(self):
        """Connect synchronously, then supervise connection health in the background."""
        with self._lock:
            self._check_open()
            self._connect_locked()
            self._start_supervisor()

    def _connect_locked(self):
        self._check_open()
        if self.connected:
            return
        client = None
        try:
            client = self._make_client()
            client.connect()
            self._client = client
            for subscription_id, spec in self._subscriptions.items():
                self._create_subscription(subscription_id, spec)
            self.connected = True
            self.last_error = None
            self._subscription_fault.clear()
            logger.info("OPC UA connected and subscriptions restored")
        except Exception as exc:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
            self._client = None
            self._remote_subscriptions.clear()
            self.last_error = "OPC UA connection failed"
            logger.debug("OPC UA connection attempt failed")
            if isinstance(exc, DependencyError):
                raise
            raise ConnectionError(self.last_error) from exc

    def _supervise(self):
        while not self._stop.is_set():
            with self._lock:
                if self._closed:
                    return
                try:
                    if self._subscription_fault.is_set():
                        self._disconnect_locked(invalidate=True)
                    if not self.connected:
                        self._connect_locked()
                    else:
                        self._client.check_connection()
                except Exception:
                    self.last_error = "OPC UA connection unavailable"
                    self._disconnect_locked(invalidate=True)
            self._stop.wait(self.config.reconnect_interval)

    def _disconnect_locked(self, *, invalidate):
        was_connected = self.connected
        self.connected = False
        client, self._client = self._client, None
        self._remote_subscriptions.clear()
        if invalidate and was_connected:
            logger.warning("OPC UA connection lost; subscribed values are unavailable")
            ua = require("asyncua.ua", "opcua")
            for subscription_id, (nodes, *_) in self._subscriptions.items():
                self._emit(
                    OpcUaEvent(
                        "disconnect",
                        subscription_id,
                        tuple(
                            OpcUaValue(node, None, ua.StatusCode(ua.StatusCodes.BadNotConnected))
                            for node in nodes
                        ),
                    )
                )
        if client is not None:
            try:
                client.disconnect()
            except Exception as exc:
                self.last_error = "OPC UA disconnect failed"
                if not invalidate:
                    raise ConnectionError(self.last_error) from exc

    def read_many(self, node_ids: Iterable[str]) -> list[OpcUaValue]:
        nodes = tuple(node_ids)
        if not nodes:
            return []
        self.connect()
        ua = require("asyncua.ua", "opcua")
        with self._lock:
            self._check_open()
            try:
                values = self._client.read_attributes(
                    [self._client.get_node(node) for node in nodes],
                    ua.AttributeIds.Value,
                )
                if len(values) != len(nodes):
                    raise RuntimeError("Unexpected OPC UA read result count")
                return [
                    OpcUaValue(
                        node,
                        getattr(dv.Value, "Value", None) if dv.StatusCode.is_good() else None,
                        dv.StatusCode,
                        dv.SourceTimestamp,
                        dv.ServerTimestamp,
                    )
                    for node, dv in zip(nodes, values, strict=True)
                ]
            except Exception as exc:
                self.last_error = "OPC UA batch read failed"
                logger.warning(self.last_error)
                self._disconnect_locked(invalidate=True)
                raise OperationError("OPC UA batch read failed") from exc

    def write_many(self, writes: Iterable[OpcUaWrite]) -> list[OpcUaWriteResult]:
        writes = tuple(writes)
        if not writes:
            return []
        ua = require("asyncua.ua", "opcua")
        # Validate types before connecting or sending any values.
        try:
            values = [
                ua.Variant(write.value, ua.VariantType[write.variant_type]) for write in writes
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid explicit OPC UA variant type or value") from exc
        self.connect()
        with self._lock:
            self._check_open()
            try:
                results = self._client.write_values(
                    [self._client.get_node(write.node_id) for write in writes],
                    values,
                    raise_on_partial_error=False,
                )
                if len(results) != len(writes):
                    raise RuntimeError("Unexpected OPC UA write result count")
                return [
                    OpcUaWriteResult(write.node_id, result)
                    for write, result in zip(writes, results, strict=True)
                ]
            except Exception as exc:
                self.last_error = "OPC UA write unconfirmed"
                logger.warning("OPC UA write unconfirmed; outcome may be uncertain")
                self._disconnect_locked(invalidate=True)
                raise OperationError("OPC UA write failed; outcome may be uncertain") from exc

    def subscribe(
        self,
        node_ids: Iterable[str],
        *,
        publishing_interval_ms: float = 500,
        sampling_interval_ms: float | Mapping[str, float] = 0,
        queue_size: int = 10,
    ) -> str:
        nodes = tuple(node_ids)
        if not nodes or len(set(nodes)) != len(nodes):
            raise ValueError("Subscription requires distinct node IDs")
        if isinstance(sampling_interval_ms, Mapping):
            sampling = dict(sampling_interval_ms)
            if set(sampling) != set(nodes):
                raise ValueError("Sampling intervals must cover exactly the subscription node IDs")
            intervals = sampling.values()
        else:
            sampling = sampling_interval_ms
            intervals = (sampling,)
        if (
            not isfinite(publishing_interval_ms)
            or publishing_interval_ms <= 0
            or any(not isfinite(interval) or interval < 0 for interval in intervals)
            or type(queue_size) is not int
            or queue_size < 1
        ):
            raise ValueError("Invalid subscription intervals or queue size")
        with self._lock:
            self._check_open()
            subscription_id = uuid4().hex
            spec = (nodes, publishing_interval_ms, sampling, queue_size)
            self._subscriptions[subscription_id] = spec
            if self.connected:
                try:
                    self._create_subscription(subscription_id, spec)
                except Exception as exc:
                    self._subscriptions.pop(subscription_id)
                    raise OperationError("OPC UA subscription failed") from exc
            return subscription_id

    def _create_subscription(self, subscription_id, spec):
        nodes, publishing, sampling, queue_size = spec
        remote = self._client.create_subscription(publishing, _Handler(self, subscription_id))
        try:
            groups = {}
            for node in nodes:
                interval = sampling[node] if isinstance(sampling, Mapping) else sampling
                groups.setdefault(interval, []).append(node)
            statuses = {}
            for interval, group in groups.items():
                results = remote.subscribe_data_change(
                    [self._client.get_node(node) for node in group],
                    sampling_interval=interval,
                    queuesize=queue_size,
                )
                if len(results) != len(group):
                    raise RuntimeError("Unexpected monitored-item result count")
                statuses.update(zip(group, results, strict=True))
            failures = tuple(
                OpcUaValue(node, None, statuses[node])
                for node in nodes
                if hasattr(statuses[node], "is_good") and not statuses[node].is_good()
            )
            if failures:
                logger.warning("OPC UA monitored-item creation partially failed")
                self._emit(OpcUaEvent("subscription_failed", subscription_id, failures))
            self._remote_subscriptions[subscription_id] = remote
        except BaseException:
            _cleanup(remote.delete, "OPC UA subscription cleanup also failed")
            raise

    def unsubscribe(self, subscription_id: str):
        with self._lock:
            self._check_open()
            remote = self._remote_subscriptions.get(subscription_id)
            if remote is not None:
                try:
                    remote.delete()
                except Exception as exc:
                    raise OperationError("OPC UA unsubscribe failed") from exc
            self._remote_subscriptions.pop(subscription_id, None)
            self._subscriptions.pop(subscription_id, None)

    def close(self):
        self._stop.set()
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                try:
                    self._disconnect_locked(invalidate=False)
                finally:
                    self._subscriptions.clear()
        finally:
            if self._supervisor is not None:
                self._supervisor.join(timeout=self.config.timeout + 2)
                if self._supervisor.is_alive():
                    raise ConnectionError(
                        "OPC UA supervisor did not stop within the shutdown timeout"
                    )

    stop = close

    def __enter__(self):
        try:
            self.connect()
        except BaseException:
            _cleanup(self.close, "Client cleanup also failed")
            raise
        return self

    def __exit__(self, *_):
        _cleanup(self.close, "Client cleanup also failed")
