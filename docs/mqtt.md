# MQTT

Install `industrial-connect[mqtt,env]` and configure `MQTT_HOST`,
`MQTT_USERNAME`, `MQTT_PASSWORD`, and `MQTT_CA_FILE`
when using a private CA. Every `MqttConfig` field uses the `MQTT_` prefix.

`client_id` is optional: each new config generates its own 23-character ID when
omitted. That ID stays the same when the client reconnects. Set `client_id` or
`MQTT_CLIENT_ID` explicitly for a stable application identity across restarts;
concurrent connections must have distinct IDs. Explicit empty IDs are rejected.

```shell
python -m examples.mqtt --env-file .env --duration 30
python -m examples.mqtt --env-file .env --duration 10 --publish-demo
```

[The example](../examples/mqtt.py) subscribes at QoS 1 to
`industrial-connect/examples/temperature` (override with `--topic`). It validates JSON
objects containing `equipment_id`, finite numeric `temperature_c` and offset-aware
`observed_at`. Invalid messages are rejected without logging their payloads. It uses
bounded waits, closes on Ctrl+C and fails if its notification queue overflowed.
`--publish-demo` explicitly sends one non-retained telemetry message; no control topic
is used. A finite monitoring window can contain no messages and still exit successfully.


`MqttClient(MqttConfig(host=..., client_id=...))` provides `start()`, `connect()`,
`subscribe(topic, qos=0)`, `unsubscribe(topic)`, `publish(topic, payload, qos=0,
retain=False, wait=False)`, and `close()`/`stop()`. MQTT 3.1.1 uses Paho callback API v2 and
its network thread. Client IDs must be unique among concurrently connected applications.

TLS is enabled by default on port 8883 with certificate and hostname verification.
Configure `ca_file`, and optionally `cert_file`/`key_file` for mutual TLS, plus
`username`/`password` as needed. Local plaintext tests must explicitly use `tls=False`
and the broker's plaintext port.

Register subscriptions before `start()` or subscribe while connected. They are replayed
after reconnect. A subscribe call accepts the local request; a later broker rejection is
reported in `last_error`. Messages preserve topic, raw bytes, QoS and retain metadata.
Applications own JSON decoding and schema validation.

`publish()` returns Paho's `MQTTMessageInfo` immediately by default. Use `wait=True`
to wait up to `config.timeout` for completion of that message. The timeout is an upper
bound, not a delay. A timeout does not cancel a queued publish. QoS 0 completion
does not mean the broker or application received the message. Paho can retransmit
in-flight messages on reconnect; the wrapper adds no replay, durable sessions, or
exactly-once guarantee. Finish publishes before closing. See
[Paho's documented delivery limitations](https://eclipse.dev/paho/clients/python/docs/).

For a stream, enqueue messages before waiting for their results so broker round trips
can overlap. The defaults allow pipelining while bounding the QoS 1/2 backlog:

| Setting | Default | Meaning |
| --- | --- | --- |
| `max_inflight_messages` | `1000` | QoS 1/2 messages awaiting acknowledgement simultaneously. |
| `max_queued_messages` | `20000` | Total outstanding QoS 1/2 messages, including in-flight messages. |

The larger window helps sustain throughput over connections with higher latency;
the queue absorbs short bursts. These are message counts, so memory use also depends
on payload size. QoS 0 is unaffected by these limits. A full queue raises
`OperationError` immediately rather than waiting for space.

Override them in `MqttConfig(...)` or with `MQTT_MAX_INFLIGHT_MESSAGES` and
`MQTT_MAX_QUEUED_MESSAGES` when using `MqttConfig.from_env()`. Both accept 0 for
unlimited, matching Paho. Throughput still depends on the broker and connection.
Topic validation is delegated to Paho;
filters registered before connection are checked when sent to the driver.

```python
with MqttClient(config) as client:
    pending = [client.publish("plant/temperature", payload, qos=1) for payload in batch]
    for info in pending:
        info.wait_for_publish(timeout=config.timeout)
        if not info.is_published():
            raise TimeoutError("MQTT publish is still unconfirmed")
```

This changes the earlier default of `wait=True`; use it explicitly where each call
must finish before proceeding.

## Queue or callback delivery

Without a callback, use the existing queue:

```python
client = MqttClient(config)
client.subscribe("plant/temperature", qos=1)
with client:
    message = client.get_notification(timeout=10)
    print(message.topic, message.payload)
```

Or provide a synchronous handler receiving one `MqttMessage`:

```python
import time

def on_message(message):
    print(message.topic, message.payload)

client = MqttClient(config, on_message=on_message)
client.subscribe("plant/temperature", qos=1)
with client:
    time.sleep(30)  # Your application runs while the worker handles messages.
```

The handler runs sequentially on an owned worker thread, outside Paho's network
thread. It can publish or use a database client; share application state with proper
synchronization and keep the database open until the MQTT worker has stopped.
Async handlers are not supported. Messages go exclusively to the handler in callback
mode: `get_notification()` raises `OperationError`, and the public notification queue
is unused. Choose one mode when constructing the client.

Handler exceptions do not kill the worker or retry the message. Monitor
`callback_error_count` and `last_callback_error` (the original exception);
`last_error` receives a safe summary. The exception itself may contain application
data. The internal callback queue uses `notification_capacity`; when full it drops
the newest message and increments `dropped_count`, just like queue mode.

`close()` stops reception and waits up to `config.timeout` for an in-flight callback.
Shutdown does not drain pending callbacks; already published messages
cannot be retracted. A blocked handler causes `ConnectionError`; let it finish and
retry `close()`. Call `close()` from the owner thread, not from inside the callback.
A callback's call to `close()` raises `OperationError`. MQTT acknowledgements do not
confirm successful application processing; use application idempotency/durability
when needed.

Run [the callback example](../examples/mqtt_callback.py) with:

```shell
python -m examples.mqtt_callback --env-file .env --duration 30
```

### Notification queues (default mode)

Both MQTT and OPC UA expose `notifications` (a standard `queue.Queue`) and
`get_notification(timeout=...)`. A timeout raises `queue.Empty`. Queues default to 1000
events; when full they drop the newest event and increment `dropped_count`, including
control events. Monitor that counter and size/drain queues appropriately. Existing
queued events remain in FIFO order across disconnects; inspect timestamps and status.
Stopping a client does not wake a consumer waiting without a timeout.
