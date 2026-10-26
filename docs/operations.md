# Deployment and verification

Create clients at application startup and close them after workers stop. Database
clients and the named registry are process-local; initialize a new set after spawning
or forking a process. Do not share a transaction session across threads. SQLite and
QuestDB serialize calls on each client. PostgreSQL and Oracle support native pools;
size them for the database's connection budget and handle acquisition timeouts.

A transaction commits only after its context exits successfully. Let failures leave
the transaction block before catching them. Never treat a commit/acknowledgement
timeout as proof that nothing happened. Decide application idempotency keys and
retention before retrying uncertain writes. QuestDB table deduplication only helps
when replayed rows keep the original deduplication keys; MQTT QoS does not make
a downstream database write exactly once.

SQLite foreign keys are enabled per connection. Rollback failure discards the
connection, and a later operation opens a new one. An in-memory database cannot
survive that discard. File location, directory creation, WAL selection and backups
remain application responsibilities.

For MQTT and OPC UA, consume notifications with finite timeouts so shutdown can
complete. Check connection state, value quality/timestamps, subscription errors and
`dropped_count`. Full queues drop the newest event, including control events; a
consumer must treat dropped notifications as a loss of evidence. A change-only PLC
subscription can legitimately remain silent: freshness requires an application
policy or heartbeat. Queued values can predate a disconnect.

TLS is enabled by default for PostgreSQL/MQTT and secured OPC UA requires a pinned
server certificate. QWP requires explicit `ws::` or `wss::`; use `wss::` in production.
Set appropriate filesystem permissions/ACLs on application private keys, DSNs,
wallets, environment files and local database/log files. Renewed OPC UA certificates
may require server trust-store approval. SQL Server encryption and FreeTDS
certificate trust must both be configured. Driver error causes and native logs may
contain credentials or SQL data even when wrapper messages do not.

## Checks

```shell
python manage.py check
python manage.py build
python tools/check_extras.py
```

The check command covers lint, formatting, type checking of the full library and
unit tests. CI repeats on Python 3.12â€“3.14 for Windows and Linux. Package installation
checks run each optional extra independently. A missing driver raises `DependencyError`
on first use with its install command. Dependency ranges are in `pyproject.toml`;
lock exact versions in the deploying application.

[Disposable integration tests](../tests/integration/README.md) exercise external
services only when explicitly selected. The OPC UA tests run a local in-process
server. Validate the actual database/server/device versions, access policies,
certificate rotation, reconnect behavior, queue sizing and outage recovery before
production use. No hardware validation or throughput guarantee follows from mocks.

Examples create only `ic_example_readings` when requested and insert only with
`--record`; SQLite also supports an in-memory run. Use an example database/schema.
MQTT publishes only with `--publish-demo`; OPC UA writes only with
`--write-double`. Production schema migrations and lifecycle decisions belong to the
application. No package import opens connections or configures the root logger. Explicit
`setup_logging()` at startup configures shared outputs for all modules; stop workers
before calling `shutdown_logging()`.
