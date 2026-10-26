# Postgres

Install `industrial-connect[postgres]` (SQLite needs only the core package).
Run from the checkout after configuring [PostgresConfig](configuration.md):

```shell
python -m examples.postgres --env-file .env --initialize --record
python -m examples.postgres --env-file .env --record --equipment MILL_1 --temperature 73.2
python -m examples.postgres --env-file .env --equipment MILL_1
```

[The example](../examples/postgres.py) models equipment temperature readings with a unique
sample ID and UTC observation time. It uses parameter binding, a transaction for each
insert and a bounded query for the ten latest readings. `--initialize` creates only the
`ic_example_readings` table; `--record` inserts one sample. The default invocation only
reads. No table is dropped. Use an example database/schema, then manage production
schemas with your application's migrations. `--env-file` requires the `env` extra;
omit it for process environment variables.

Read [database lifetimes, transactions and errors](databases.md) before using the client
in a worker or service. Never replay a failed write without determining its outcome.

Required: `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`. Optional: `PGPORT` (5432),
`PGPOOL=true`, `PGPOOL_MIN`, `PGPOOL_MAX`, `PGPOOL_TIMEOUT`, `PGCONNECT_TIMEOUT`,
`PGSSLMODE` (`verify-full`), `PGSSLROOTCERT`. Use a trusted CA and matching hostname.
Native Psycopg pools are lazy; pool exhaustion raises `ConnectionError`. Plaintext
requires an explicit `PGSSLMODE=disable` for a disposable local service.
