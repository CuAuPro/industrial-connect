# MQTT

Install `industrial-connect[mqtt,env]`. Set `MQTT_HOST` and, as needed,
`MQTT_USERNAME`, `MQTT_PASSWORD` and `MQTT_CA_FILE` in `.env`.
All `MqttConfig` fields use the `MQTT_` environment prefix.

## Publish: typed packets, custom JSON or raw

`publish()` accepts strings or bytes. Serialize dictionaries with `json.dumps()`;
`MqttPacket` is optional. No subscription is needed.

```python
import json

from industrial_connect.mqtt import MqttClient, MqttConfig, MqttPacket, Uom

with MqttClient(MqttConfig.from_env(env_file=".env")) as client:
    # One measurement per topic, with timestamp and unit.
    packet = MqttPacket(value=72.5, uom=Uom.degC)
    client.publish("plant/mill-1/temperature", packet.to_json(), qos=1, wait=True)

    # Custom key:value JSON: choose your own keys and structure.
    client.publish("plant/status", json.dumps({"running": True, "speed": 120}), wait=True)

    # Raw text or bytes.
    client.publish("plant/message", "hello", wait=True)
    client.publish("plant/raw", b"\x01\x02", wait=True)
```

`wait=True` finishes each publish before closing, bounded by `config.timeout`.
For QoS 1 this waits for the broker acknowledgement; QoS 0 completion does not
confirm receipt. A timeout does not cancel a queued publish.

### Measurement format and units

`MqttPacket` is this library's optional application payload convention. The topic
identifies the measurement. Example JSON:

```json
{"value": 72.5, "timestamp": "2026-10-28T10:00:00Z", "uom": "degC"}
```

- `value`: string, integer, finite float, boolean or `None` (JSON `null`).
- `timestamp`: defaults to current UTC; an explicit timezone-aware `datetime` is
  converted to UTC ISO 8601 with `Z`, preserving microseconds.
- `uom`: `Uom` member or custom nonblank string; omitted from JSON when `None`.

```python
from datetime import datetime

packet = MqttPacket(
    value=125,
    timestamp=datetime.fromisoformat("2026-10-28T11:00:00+01:00"),
    uom=Uom.mm,
)
custom = MqttPacket(value=120, uom="widgets/hour")
unitless = MqttPacket(value=True)
```

`Uom` is a string enum with autocomplete. `Uom("t")` looks up a member by value;
iterate over `Uom` to list units. JSON values match names except where shown:

| Category | Members |
| --- | --- |
| Length | `mm`, `cm`, `m`, `km` |
| Mass | `g`, `kg`, `tonne` → `"t"` (metric ton) |
| Time | `s`, `min`, `h` |
| Temperature | `degC`, `degF`, `K` |
| Pressure | `Pa`, `kPa`, `bar` |
| Other | `percent` → `"%"`, `rpm`, `Hz`, `V`, `A`, `W`, `kW`, `kWh` |

Packets are frozen and keyword-only. Invalid types, nonfinite numbers, naive
timestamps and blank units are rejected at construction. Units are labels, without
conversion or dimensional checks. The helper does not parse incoming packets or
package multiple measurements; use custom JSON for your own structure.

## Connection and delivery

- `with MqttClient(config)` connects and closes automatically. Explicit lifecycle:
  `start()`/`connect()` and `close()`/`stop()`.
- TLS defaults to port 8883 with certificate and hostname verification. Use
  `ca_file` for a private CA, `cert_file`/`key_file` for mutual TLS and
  `username`/`password` for authentication. Plaintext requires `tls=False` and
  the broker's plaintext port.
- Omitted `client_id` generates a unique 23-character ID, reused on reconnect.
  Set `client_id`/`MQTT_CLIENT_ID` for an identity across restarts. Concurrent
  clients need distinct IDs; empty IDs are rejected.
- `publish(topic, payload, qos=0, retain=False, wait=False)` returns Paho's
  `MQTTMessageInfo`. Finish publishes before closing. Paho may retransmit on
  reconnect; the wrapper adds no replay, durable sessions or exactly-once guarantee.
  See [Paho delivery limitations](https://eclipse.dev/paho/clients/python/docs/).
- MQTT 3.1.1 uses Paho callback API v2 and its network thread. Topic validation
  is delegated to Paho; pre-connection filters are checked when sent.

### Publish streams

Queue messages before waiting so broker round trips overlap:

```python
with MqttClient(config) as client:
    pending = [client.publish("plant/temperature", payload, qos=1) for payload in batch]
    for info in pending:
        info.wait_for_publish(timeout=config.timeout)
        if not info.is_published():
            raise TimeoutError("MQTT publish is still unconfirmed")
```

| Config field | Default | QoS 1/2 limit |
| --- | --- | --- |
| `max_inflight_messages` | `1000` | Messages awaiting acknowledgement simultaneously |
| `max_queued_messages` | `20000` | Total outstanding messages, including in-flight |

Override via `MqttConfig` or `MQTT_MAX_INFLIGHT_MESSAGES` /
`MQTT_MAX_QUEUED_MESSAGES`; `0` means unlimited. QoS 0 is unaffected. A full queue
raises `OperationError` immediately. Limits count messages, so payload size affects
memory use. Throughput depends on broker and connection. The default is `wait=False`;
set `wait=True` explicitly when each call must complete before proceeding.

## Subscribe: queue or callback

Register subscriptions before connecting or while connected; they replay after
reconnect. `subscribe(topic, qos=0)` accepts the local request; later broker
rejections appear in `last_error`. Use `unsubscribe(topic)` to remove one.
Messages contain topic, raw payload bytes, QoS and retain metadata. Applications
own JSON decoding and validation.

```python
client = MqttClient(config)
client.subscribe("plant/temperature", qos=1)
with client:
    message = client.get_notification(timeout=10)
    print(message.topic, message.payload)
```

Or supply a synchronous handler receiving one `MqttMessage`:

```python
import time


def on_message(message):
    print(message.topic, message.payload)


client = MqttClient(config, on_message=on_message)
client.subscribe("plant/temperature", qos=1)
with client:
    time.sleep(30)
```

- Callbacks run sequentially on an owned worker, outside Paho's network thread.
  They may publish or use a database; synchronize shared state and keep the database
  open until the worker stops. Async handlers are unsupported.
- Callback mode delivers exclusively to the handler: `get_notification()` raises
  `OperationError`, and the public notification queue is unused.
- Handler exceptions do not stop the worker or retry messages. Monitor
  `callback_error_count`, `last_callback_error` (which may contain application data)
  and `last_error` (a safe summary). MQTT acknowledgement does not confirm successful
  application processing.
- `close()` waits up to `config.timeout` for an active callback and discards pending
  callbacks. A blocked handler causes `ConnectionError`; let it finish, then retry
  closing. Close from the owner thread; calling it inside a callback raises
  `OperationError`. Published messages cannot be retracted.
- Both delivery modes use `notification_capacity` (default `1000`), dropping the
  newest event on overflow and incrementing `dropped_count`. Monitor that counter.
  Queue mode exposes `notifications` (`queue.Queue`); `get_notification()` times out
  with `queue.Empty`. Queued events, including control events, remain FIFO across
  disconnects. Check timestamps/status; stopping does not wake an unbounded wait.

## Runnable examples

```shell
python -m examples.mqtt --env-file .env --duration 30
python -m examples.mqtt --env-file .env --duration 10 --publish-demo
python -m examples.mqtt_callback --env-file .env --duration 30
```

The [queue](../examples/mqtt.py) and [callback](../examples/mqtt_callback.py)
examples use their own telemetry schema: `equipment_id`, finite `temperature_c`
and offset-aware `observed_at`. They subscribe at QoS 1 to
`industrial-connect/examples/temperature` (override with `--topic`), reject invalid
payloads without logging them, use bounded waits, close on Ctrl+C and fail on queue
overflow. `--publish-demo` sends one non-retained reading. A monitoring window with
no messages can still succeed.
