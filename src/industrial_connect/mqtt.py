import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from queue import Full, Queue
from threading import Event, RLock, Thread, current_thread
from typing import Any
from uuid import uuid4

from ._notifications import Notifications
from ._optional import require
from .config import EnvConfig
from .errors import ConnectionError, DependencyError, OperationError, _cleanup
from .logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class MqttConfig(EnvConfig):
    _env_prefix = "MQTT_"

    host: str
    client_id: str = field(default_factory=lambda: "ic" + uuid4().hex[:21])
    port: int = 8883
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    keepalive: int = 60
    timeout: float = 10.0
    reconnect_min: int = 1
    reconnect_max: int = 30
    notification_capacity: int = 1000
    max_inflight_messages: int = 1000
    max_queued_messages: int = 20000
    tls: bool = True
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    key_password: str | None = field(default=None, repr=False)

    def __post_init__(self):
        self._validate_numbers()
        if not self.host or not self.client_id or "/" in self.host or "@" in self.host:
            raise ValueError("host and client_id are required")
        if (
            not 0 < self.port < 65536
            or min(self.keepalive, self.timeout, self.notification_capacity) <= 0
        ):
            raise ValueError("Invalid port, keepalive, timeout or capacity")
        if not 0 < self.reconnect_min <= self.reconnect_max:
            raise ValueError("Invalid reconnect bounds")
        if min(self.max_inflight_messages, self.max_queued_messages) < 0:
            raise ValueError("MQTT message limits must be nonnegative")
        if bool(self.cert_file) != bool(self.key_file):
            raise ValueError("cert_file and key_file must be supplied together")
        if not self.tls and any((self.ca_file, self.cert_file, self.key_file)):
            raise ValueError("TLS files require tls=True")


@dataclass(frozen=True)
class MqttMessage:
    topic: str
    payload: bytes
    qos: int
    retain: bool


class MqttClient(Notifications[MqttMessage]):
    """MQTT 3.1.1 with Paho's network thread and in-memory reconnect behavior."""

    def __init__(
        self, config: MqttConfig, *, on_message: Callable[[MqttMessage], None] | None = None
    ):
        if on_message is not None and not callable(on_message):
            raise TypeError("on_message must be callable")
        if iscoroutinefunction(on_message) or iscoroutinefunction(
            getattr(on_message, "__call__", None)
        ):
            raise TypeError("on_message must be a synchronous callable")
        super().__init__(config.notification_capacity)
        self.config = config
        self.connected = False
        self.last_error: str | None = None
        self._client: Any = None
        self._closed = False
        self._lock = RLock()
        self._connack = Event()
        self._subscriptions: dict[str, int] = {}
        self._message_handler = on_message
        self._callback_queue: Queue[MqttMessage | None] = Queue(config.notification_capacity)
        self._callback_stop = Event()
        self._callback_worker: Thread | None = None
        self.callback_error_count = 0
        self.last_callback_error: Exception | None = None

    def get_notification(self, timeout: float | None = None) -> MqttMessage:
        if self._message_handler is not None:
            raise OperationError("MQTT callback mode delivers messages to on_message")
        return super().get_notification(timeout=timeout)

    def _dispatch_messages(self) -> None:
        while not self._callback_stop.is_set():
            message = self._callback_queue.get()
            if message is None or self._callback_stop.is_set():
                return
            try:
                assert self._message_handler is not None
                self._message_handler(message)
            except Exception as exc:
                self.callback_error_count += 1
                self.last_callback_error = exc
                self.last_error = "MQTT on_message callback failed"
                logger.warning("MQTT on_message callback failed; inspect last_callback_error")

    def _join_callback_worker(self) -> None:
        worker = self._callback_worker
        if worker is not None:
            worker.join(timeout=self.config.timeout)
            if worker.is_alive():
                raise ConnectionError("MQTT on_message callback did not stop within the timeout")
            self._callback_worker = None

    @property
    def connection_status(self) -> str:
        return "CLOSED" if self._closed else "CONNECTED" if self.connected else "DISCONNECTED"

    def _make_client(self):
        driver = require("paho.mqtt.client", "mqtt")
        c = self.config
        client = driver.Client(
            driver.CallbackAPIVersion.VERSION2, client_id=c.client_id, protocol=driver.MQTTv311
        )
        if c.username:
            client.username_pw_set(c.username, c.password)
        if c.tls:
            context = ssl.create_default_context(cafile=c.ca_file)
            if c.cert_file:
                context.load_cert_chain(c.cert_file, c.key_file, c.key_password)
            client.tls_set_context(context)
        client.connect_timeout = c.timeout
        client.reconnect_delay_set(c.reconnect_min, c.reconnect_max)
        client.max_inflight_messages_set(c.max_inflight_messages)
        client.max_queued_messages_set(c.max_queued_messages)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.on_unsubscribe = self._on_unsubscribe
        return client

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise ConnectionError("MQTT client is closed")
            if self._client is not None:
                return
            client = None
            try:
                client = self._make_client()
                client.connect_async(self.config.host, self.config.port, self.config.keepalive)
                result = client.loop_start()
                if result != 0:
                    raise RuntimeError("Paho network loop failed to start")
                if self._message_handler is not None:
                    worker = Thread(
                        target=self._dispatch_messages, name="industrial-connect-mqtt", daemon=True
                    )
                    worker.start()
                    self._callback_worker = worker
                self._client = client
            except Exception as exc:
                if client is not None:
                    _cleanup(
                        client.disconnect, "MQTT disconnect during startup cleanup also failed"
                    )
                    _cleanup(client.loop_stop, "MQTT loop cleanup also failed")
                if isinstance(exc, DependencyError):
                    raise
                raise ConnectionError("Could not start MQTT client") from exc

    def connect(self) -> None:
        self.start()
        if not self._connack.wait(self.config.timeout) or not self.connected:
            self.last_error = "MQTT connection was not acknowledged successfully"
            raise ConnectionError(self.last_error)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self._lock:
            if self._closed:
                return
            self.connected = reason_code == 0
            self.last_error = None if self.connected else "MQTT broker rejected connection"
            if self.connected:
                logger.info("MQTT connected")
                for topic, qos in self._subscriptions.items():
                    try:
                        result, _ = client.subscribe(topic, qos=qos)
                        if result != 0:
                            self.last_error = "MQTT subscription request failed"
                    except Exception:
                        self.last_error = "MQTT subscription request failed"
            self._connack.set()
            if not self.connected:
                logger.warning("MQTT broker rejected the connection")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        self._connack.clear()
        if not self._closed:
            self.last_error = "MQTT connection lost"
            logger.warning("MQTT connection lost; reconnect will be attempted")

    def _on_message(self, client, userdata, message):
        if self._closed:
            return
        event = MqttMessage(
            message.topic, bytes(message.payload), message.qos, bool(message.retain)
        )
        if self._message_handler is None:
            self._emit(event)
        else:
            try:
                self._callback_queue.put_nowait(event)
            except Full:
                with self._notification_lock:
                    self.dropped_count += 1

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(getattr(code, "is_failure", False) for code in reason_codes):
            self.last_error = "MQTT broker rejected a subscription"
            logger.warning(self.last_error)

    def _on_unsubscribe(self, client, userdata, mid, reason_codes, properties):
        if any(getattr(code, "is_failure", False) for code in reason_codes):
            self.last_error = "MQTT broker rejected an unsubscribe request"

    def subscribe(self, topic: str, qos: int = 0) -> None:
        if qos not in (0, 1, 2):
            raise ValueError("A topic and QoS 0, 1 or 2 are required")
        with self._lock:
            if self._closed:
                raise ConnectionError("MQTT client is closed")
            if self.connected:
                try:
                    result, _ = self._client.subscribe(topic, qos=qos)
                    if result != 0:
                        raise RuntimeError("MQTT subscribe returned an error")
                except Exception as exc:
                    raise OperationError("MQTT subscription failed") from exc
            self._subscriptions[topic] = qos

    def unsubscribe(self, topic: str) -> None:
        with self._lock:
            if self._closed:
                raise ConnectionError("MQTT client is closed")
            if self.connected:
                try:
                    result, _ = self._client.unsubscribe(topic)
                    if result != 0:
                        raise RuntimeError("MQTT unsubscribe returned an error")
                except Exception as exc:
                    raise OperationError("MQTT unsubscribe failed") from exc
            self._subscriptions.pop(topic, None)

    def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int = 0,
        retain: bool = False,
        wait: bool = False,
    ):
        """Queue a Paho publish; optionally wait for completion with wait=True."""
        with self._lock:
            if self._closed or not self.connected:
                raise ConnectionError("MQTT client is not connected")
            try:
                info = self._client.publish(topic, payload, qos=qos, retain=retain)
                if info.rc != 0:
                    raise RuntimeError("MQTT publish was not accepted")
            except Exception as exc:
                logger.warning("MQTT publish request failed")
                raise OperationError("MQTT publish failed") from exc
        if wait:
            try:
                info.wait_for_publish(timeout=self.config.timeout)
                if not info.is_published():
                    raise TimeoutError("Publish acknowledgement timed out")
            except Exception as exc:
                logger.warning("MQTT publish unconfirmed; delivery may be uncertain")
                raise OperationError("MQTT publish unconfirmed; delivery may be uncertain") from exc
        return info

    def close(self) -> None:
        with self._lock:
            if current_thread() is self._callback_worker:
                raise OperationError("Close the MQTT client from its owner, outside on_message")
            if self._closed and self._client is None and self._callback_worker is None:
                return
            self._closed = True
            self._callback_stop.set()
            if self._callback_worker is not None:
                try:
                    self._callback_queue.put_nowait(None)
                except Full:
                    pass
            client = self._client
            self.connected = False
            self._connack.clear()
        try:
            if client is not None:
                try:
                    try:
                        client.disconnect()
                    finally:
                        _cleanup(client.loop_stop, "MQTT network loop shutdown also failed")
                except Exception as exc:
                    raise ConnectionError("Could not stop MQTT client") from exc
                with self._lock:
                    self._client = None
        finally:
            _cleanup(self._join_callback_worker, "MQTT callback worker cleanup also failed")
        logger.info("MQTT client stopped")

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
