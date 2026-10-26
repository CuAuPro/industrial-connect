# Oracle

Install `industrial-connect[oracle]` (SQLite needs only the core package).
Run from the checkout after configuring [OracleConfig](configuration.md):

```shell
python -m examples.oracle --env-file .env --initialize --record
python -m examples.oracle --env-file .env --record --equipment MILL_1 --temperature 73.2
python -m examples.oracle --env-file .env --equipment MILL_1
```

[The example](../examples/oracle.py) models equipment temperature readings with a unique
sample ID and UTC observation time. It uses parameter binding, a transaction for each
insert and a bounded query for the ten latest readings. `--initialize` creates only the
`ic_example_readings` table; `--record` inserts one sample. The default invocation only
reads. No table is dropped. Use an example database/schema, then manage production
schemas with your application's migrations. `--env-file` requires the `env` extra;
omit it for process environment variables.

Read [database lifetimes, transactions and errors](databases.md) before using the client
in a worker or service. Never replay a failed write without determining its outcome.

Required: `ORACLE_DSN`, `ORACLE_USER`, `ORACLE_PASSWORD`. Optional: `ORACLE_POOL=true`,
`ORACLE_CONNECT_TIMEOUT`, `ORACLE_CALL_TIMEOUT_MS`, `ORACLE_POOL_TIMEOUT_MS`,
`ORACLE_POOL_MIN`, `ORACLE_POOL_MAX`. Configure TCPS/wallet settings in the native DSN.
Thin mode is the default; call `init_oracle_thick(lib_dir=...)` once before connecting
when using Oracle Client libraries. `--initialize` is a one-time operation and Oracle
DDL implicitly commits. The query uses `FETCH FIRST`, requiring Oracle 12c or newer.

`python -m examples.oracle_plsql --env-file .env` shows numeric OUT binds and a REF
CURSOR in one transaction, without creating a stored procedure. See
[Oracle PL/SQL semantics](databases.md#oracle-plsql). Large objects and custom Oracle
objects retain native driver behavior; consume connection-dependent values inside an
explicit transaction while the connection is still active.
