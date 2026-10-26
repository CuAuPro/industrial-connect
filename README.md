# industrial-connect

Reusable Python connectors for industrial applications. Python 3.12+, Windows and Linux.
Optional drivers, explicit resource ownership, no import-time connections.

## Install

```shell
pip install industrial-connect                    # SQLite and core
pip install "industrial-connect[postgres,env]"
pip install "industrial-connect[mqtt,opcua]"
pip install "industrial-connect[all]"
```

Install only the drivers you need. Pin the tested library version and lock your
application's dependencies. `db` includes every database; `all` includes every extra.

## Connectors and runnable examples

Each guide includes configuration, a realistic example, resource lifetime and error behavior.
Run examples from the checkout; `--help` lists the available options.

| Connector | Extra | Guide | Example |
| --- | --- | --- | --- |
| PostgreSQL | `postgres` | [PostgreSQL](docs/postgres.md) | `python -m examples.postgres` |
| Oracle | `oracle` | [Oracle and PL/SQL](docs/oracle.md) | `python -m examples.oracle` |
| SQL Server | `mssql` | [SQL Server](docs/mssql.md) | `python -m examples.mssql` |
| SQLite | none | [SQLite](docs/sqlite.md) | `python -m examples.sqlite` |
| QuestDB QWP | `questdb` | [WebSocket ingestion and SQL](docs/questdb.md) | `python -m examples.questdb` |
| MQTT | `mqtt` | [Queue or callback telemetry](docs/mqtt.md) | `python -m examples.mqtt` / `examples.mqtt_callback` |
| OPC UA | `opcua` | [PLC reads, writes and subscriptions](docs/opcua.md) | `python -m examples.opcua` |
| Console/files | none | [Logging](docs/logging.md) | `python -m examples.logging_setup` |
| Graylog | `graylog` | [GELF/UDP](docs/graylog.md) | `python -m examples.graylog` |

## Quick start without a service

```python
from industrial_connect.db import SqliteClient, SqliteConfig

with SqliteClient(SqliteConfig(database=":memory:")) as db:
    db.execute("CREATE TABLE readings(equipment TEXT, temperature_c REAL)")
    with db.transaction() as tx:
        tx.execute("INSERT INTO readings VALUES (?, ?)", ("MILL_1", 72.5))
    print(db.query("SELECT * FROM readings WHERE equipment=?", ("MILL_1",)))
```

Every config supports explicit constructors and `from_env()`; loading a `.env` file
requires the `env` extra and an explicit `env_file` argument. See
[configuration](docs/configuration.md) and the [sample environment](examples/.env.example).
For persistent clients shared across modules, see [the sharing guide](examples/README.md).

## Operational contracts

- Close clients after workers stop. Context managers own cleanup; closing is terminal.
- Use bound SQL parameters. Native placeholders differ by database; see the guides.
- Catch `IndustrialConnectError` or its `DependencyError`, `ConnectionError` and
  `OperationError` subclasses. Native errors remain in exception chains; cleanup
  failures add notes instead of hiding the original failure.
- A failed write may already have arrived. The wrapper does not retry writes; native
  MQTT and QuestDB drivers have their own delivery/reconnect behavior.
- MQTT/OPC UA queues are bounded. Monitor `dropped_count`; overflow means data loss.
- Configure logging once with `setup_logging()`; modules use `get_logger(__name__)`.
- Configure TLS, certificate trust and credentials for your deployment. Avoid logging
  secrets and raw driver exception chains. Graylog GELF/UDP is unencrypted.

See [database contracts](docs/databases.md) and [deployment and verification](docs/operations.md).

## Develop and publish

```shell
python -m pip install -e ".[all,dev]"
python manage.py check
python manage.py build
```

The build command runs checks and creates a wheel and source archive in `dist/`.
Uploads are explicit. See [publishing](docs/publishing.md) for version bumps,
TestPyPI, PyPI and private registries, and [integration tests](tests/integration/README.md)
for disposable services. Passing unit tests alone does not validate your plant's devices,
network, database permissions or delivery requirements.

Licensed under [Apache-2.0](LICENSE). SQLite defaults to `store.db`;
`SqliteConfig.from_env()` accepts `SQLITE_DATABASE` to choose another file or `:memory:`.
