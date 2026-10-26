# SQLite

Install `industrial-connect[sqlite]` (SQLite needs only the core package).
Run from the checkout after configuring [SqliteConfig](configuration.md):

```shell
python -m examples.sqlite --env-file .env --initialize --record
python -m examples.sqlite --env-file .env --record --equipment MILL_1 --temperature 73.2
python -m examples.sqlite --env-file .env --equipment MILL_1
```

[The example](../examples/sqlite.py) models equipment temperature readings with a unique
sample ID and UTC observation time. It uses parameter binding, a transaction for each
insert and a bounded query for the ten latest readings. `--initialize` creates only the
`ic_example_readings` table; `--record` inserts one sample. The default invocation only
reads. No table is dropped. Use an example database/schema, then manage production
schemas with your application's migrations. `--env-file` requires the `env` extra;
omit it for process environment variables.

Read [database lifetimes, transactions and errors](databases.md) before using the client
in a worker or service. Never replay a failed write without determining its outcome.

Configuration: `SQLITE_DATABASE` (default `store.db`), `SQLITE_TIMEOUT` (10 seconds).
`store.db` is relative to the working directory. Set `SQLITE_DATABASE` to override it;
the parent directory must exist. Set `SQLITE_DATABASE=:memory:` for temporary storage.
Environment overrides are loaded through `SqliteConfig.from_env()`, as in the example;
`SqliteClient()` uses the plain config defaults without reading the environment.
One client serializes operations across threads. Foreign keys are enabled. Nested
transactions are rejected. A connection discarded after rollback failure is reopened
on the next operation; an in-memory database then loses its previous contents. WAL
mode and filesystem backup/retention are application choices.
