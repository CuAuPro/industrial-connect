# SQLite

Install `industrial-connect`.
Configure [SqliteConfig](configuration.md) and run the example:

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

## Native connections for application-owned transactions

```python
import sqlite3
from industrial_connect.db import SqliteConfig, sqlite_connection

with sqlite_connection(SqliteConfig(database="process.db", timeout=30),
                       row_factory=sqlite3.Row) as connection:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY)")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
```

The context owns opening/closing, foreign keys and connection timeout. It neither
begins nor commits transactions; the default is native SQLite autocommit. Closing rolls
back unfinished transactions. Do not retain a connection outside its context or share it
between operations. `SqliteClient` provides the higher-level serialized transaction API
and retains its connection for `:memory:` databases. Both use the same connection setup.
