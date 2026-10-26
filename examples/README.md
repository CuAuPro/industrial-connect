# Examples

See the [connector index](../README.md#connectors-and-runnable-examples) for one runnable
example and guide per connector. External database examples read by default; use
`--initialize --record` only on an approved example database. Copy
[`.env.example`](.env.example) to your own `.env`, replace placeholders and install
`env` when passing `--env-file`. Sample values are not production credentials.

## Sharing clients across files and callbacks

Register clients by name at startup, then import `get_database()` in any file.

```python
# main.py
from industrial_connect.db import SqliteClient, SqliteConfig, init_database, close_all_databases

try:
    production = init_database("production", SqliteClient(SqliteConfig(database=":memory:")))
    reporting = init_database("reporting", SqliteClient(SqliteConfig(database=":memory:")))
    production.execute("CREATE TABLE mqtt_messages(topic TEXT, payload BLOB)")
    reporting.execute("CREATE TABLE reports(value REAL)")
    # Run MQTT, HTTP, etc. Stop workers before the finally block.
finally:
    close_all_databases()
```

```python
# mqtt_callbacks.py (or any other module)
from industrial_connect.db import get_database


def on_message(mqtt_client, userdata, message):
    get_database("production").execute(
        "INSERT INTO mqtt_messages(topic, payload) VALUES (?, ?)",
        (message.topic, bytes(message.payload)),
    )
```

```python
# http_handlers.py
from industrial_connect.db import get_database


def get_reports():
    return get_database("reporting").query("SELECT value FROM reports")
```

The name selects the actual registered object; no database argument, `client_type`, or
MQTT `userdata` setup is needed. Oracle works the same way:
`oracle = init_database("production", OracleClient(config))`.
The returned startup variable retains its specific type. Named lookups return `Any`,
so native methods work, but editors cannot infer Oracle from an arbitrary name string.

Registration opens no connections. Blank/duplicate names and client aliases are rejected.
Unknown lookups raise `KeyError`. Call `close_database("production")` to close one, or
`close_all_databases()` to close all. Close only after workers stop; failed closures remain
registered for retry, and close-all groups failures in an `ExceptionGroup`. The registry
is process-local: initialize clients separately after creating each application process.

Run the two-database example without external services:

```shell
python -m examples.shared_client.named_main
```

See the [named startup example](shared_client/named_main.py),
[MQTT callback](shared_client/mqtt_callbacks.py), and [HTTP handlers](shared_client/http_handlers.py).
For a live MQTT broker, run:

```shell
python -m examples.shared_client.mqtt_main
```

Install `industrial-connect[mqtt,env]` first. Configure `MQTT_HOST`,
optional `MQTT_CLIENT_ID`/`MQTT_USERNAME`/`MQTT_PASSWORD`, and your TLS CA.
SQLite defaults to `store.db`; set `SQLITE_DATABASE` to use another file. For a bounded run:

```shell
python -m examples.shared_client.mqtt_main --env-file .env --duration 30
```

The application consumes `MqttClient.get_notification()` and calls the handler on
its own thread. Slow database work never runs in Paho's network callback. Stop
consumers before closing the named database; a database failure ends this example
rather than claiming the message was stored. MQTT acknowledgements are not a
transaction with SQLite; this example is not a durable exactly-once pipeline.

## Configuration

Use `Config.from_env(env_file=".env")` with an explicit path, or omit `env_file` for
process variables only. Existing constructors continue to work. Precedence is
explicit arguments, process environment, file values, then defaults. See
[configuration](../docs/configuration.md).
