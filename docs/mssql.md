# MSSQL

Install `industrial-connect[mssql]` (SQLite needs only the core package).
Run from the checkout after configuring [SqlServerConfig](configuration.md):

```shell
python -m examples.mssql --env-file .env --initialize --record
python -m examples.mssql --env-file .env --record --equipment MILL_1 --temperature 73.2
python -m examples.mssql --env-file .env --equipment MILL_1
```

[The example](../examples/mssql.py) models equipment temperature readings with a unique
sample ID and UTC observation time. It uses parameter binding, a transaction for each
insert and a bounded query for the ten latest readings. `--initialize` creates only the
`ic_example_readings` table; `--record` inserts one sample. The default invocation only
reads. No table is dropped. Use an example database/schema, then manage production
schemas with your application's migrations. `--env-file` requires the `env` extra;
omit it for process environment variables.

Read [database lifetimes, transactions and errors](databases.md) before using the client
in a worker or service. Never replay a failed write without determining its outcome.

Required: `MSSQL_SERVER`, `MSSQL_DATABASE`, `MSSQL_USER`, `MSSQL_PASSWORD`. Optional:
`MSSQL_PORT` (1433), `MSSQL_LOGIN_TIMEOUT` (10 seconds), `MSSQL_QUERY_TIMEOUT`
(30 seconds), `MSSQL_ENCRYPTION` (`require`). There is no wrapper connection pool.
Certificate verification is controlled by your FreeTDS configuration; encryption alone
is not a certificate trust policy. pymssql timeouts can affect the process; keep them
consistent. The example stores UTC without an offset in DATETIME2; consumers must
interpret `observed_at` as UTC.
