# OPC UA

Install `industrial-connect[opcua,env]`. Configure `OPCUA_ENDPOINT`,
`OPCUA_APPLICATION_URI`, `OPCUA_CERTIFICATE`, `OPCUA_PRIVATE_KEY` and
`OPCUA_SERVER_CERTIFICATE`; use `OPCUA_USERNAME`/`OPCUA_PASSWORD` if required.
Obtain the server certificate through your trusted provisioning process and register
the client certificate on the PLC/server. Automatic local certificate generation
does not establish server trust.

```shell
python -m examples.opcua --env-file .env --node "ns=2;s=Temperature" --duration 30
python -m examples.opcua --env-file .env --node "ns=2;s=Temperature" --node "ns=2;s=Speed"
```

[The example](../examples/opcua.py) takes an initial batch read, then monitors changes
with native status codes and source/server timestamps. It reports disconnect and
subscription failures, treats queue overflow as data loss, and uses bounded waits.
Writes require explicitly supplying `--write-double VALUE` and exactly one `--node`;
use only an approved writable test node. Every write result is checked.


```python
from industrial_connect.opcua import OpcUaClient, OpcUaConfig, OpcUaWrite

config = OpcUaConfig(
    endpoint="opc.tcp://your-server:4840",
    application_uri="urn:your-company:monitor",
    certificate="client.der", private_key="client-key.pem",
    server_certificate="trusted-server.der",
)
with OpcUaClient(config) as client:
    node = "ns=2;s=Temperature"
    print(client.read_many([node]))
    subscription = client.subscribe([node], publishing_interval_ms=500)
    event = client.get_notification(timeout=10)
    client.unsubscribe(subscription)
    # Explicit write example, only for a node you intend to modify:
    # print(client.write_many([OpcUaWrite(node, 20.0, "Double")]))
```

`connect()` waits for a connection and starts a health supervisor. `start()` connects in
the background. Reads/writes connect automatically. `close()`/`stop()` disconnect and stop
owned threads. `connected`, `connection_status` and `last_error` report connection state.
Background connection errors are exposed through status; synchronous calls raise errors.

Batch reads preserve node IDs, native status codes and UTC source/server timestamps.
Bad-status values are returned as `None`. Writes require explicit `VariantType` names;
inspect each returned status code because a batch can partially fail. Writes are never
replayed by the wrapper. A transport error raises an error with an uncertain outcome.

Subscriptions can be registered before connection and are restored after reconnect.
Use a numeric `sampling_interval_ms` for one rate, or a mapping covering exactly the
subscribed node IDs for mixed rates within one subscription:

```python
subscription = client.subscribe(
    ["ns=2;s=Speed", "ns=2;s=Temperature"],
    publishing_interval_ms=250,
    sampling_interval_ms={"ns=2;s=Speed": 50, "ns=2;s=Temperature": 1000},
    queue_size=10,
)
```

Sampling intervals must be finite and nonnegative; zero requests the server's fastest
practical rate. The server may revise requested intervals. Settings are copied when
registered and restored after reconnect. One unsubscribe removes all rate groups.

`subscribe()` returns a local subscription ID; `unsubscribe(id)` deletes its remote
subscription. Events have `kind` (`data`, `subscription_failed`, `disconnect`),
`subscription_id` and `values`. Disconnect events invalidate subscribed values with
`BadNotConnected`. The library stores no last-value cache. Server-side monitored-item
failures appear individually as `subscription_failed` events.

The default policy is `Basic256Sha256` with `SignAndEncrypt`. Other supported secure
policies are `Aes128Sha256RsaOaep` and `Aes256Sha256RsaPss`. All secure policies require
application certificates and an explicitly pinned server certificate. Validation checks
the server certificate fingerprint, validity period, application URI and server extended
key usage. Pin renewal is an application deployment action; there is no automatic trust
on first use. For an explicitly unsecured local test use `security_policy="None"`.

Applications own signal conversion, freshness rules and identity handling. The connector
preserves native values, status codes and timestamps; it does not infer process meaning.
Historian schemas and delivery/retry policy likewise belong to the consuming application.

Certificate generation is explicit and does not contact a server:

```python
from industrial_connect.opcua import generate_application_certificate

generate_application_certificate(
    "client-key.pem", "client.der", application_uri="urn:your-company:monitor",
)
```

Secure clients prepare the application certificate in the object's constructor by default:

```python
client = OpcUaClient(OpcUaConfig(
    endpoint="opc.tcp://your-server:4840",
    application_uri="urn:your-company:monitor",
    certificate="client.der", private_key="client-key.pem",
    server_certificate="trusted-server.der",
    # auto_generate_certificate=False,  # use externally managed files instead
    # force_certificate_regeneration=True,  # replace even a valid certificate
))
```

This writes local files during initialization and makes no server connection.
Unsecured connections (`security_policy="None"`) skip certificate preparation.
Valid files are reused; missing/expired certificates are generated or renewed. The force
flag implies generation and replaces the certificate while retaining an existing private
key. It applies once per object initialization, never on reconnect. The standalone helper
also accepts `force=True`. Automatic generation supports unencrypted private keys;
password-protected keys can still be supplied with `auto_generate_certificate=False`.
The server certificate is always supplied separately and is never generated or overwritten.
Renewed client certificates may need approval in the server's trust store.
Protect the generated private key with appropriate filesystem permissions and register
the application certificate in the server's trust store.
