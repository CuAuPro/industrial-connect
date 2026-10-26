# Relational databases

```python
from industrial_connect.db import SqliteClient, SqliteConfig

with SqliteClient(SqliteConfig(database=":memory:")) as db:
    db.execute("create table readings (value real)")
    with db.transaction() as tx:
        tx.execute("insert into readings values (?)", (1.5,))
        tx.executemany("insert into readings values (?)", [(2.5,), (3.5,)])
    print(db.query("select * from readings where value > ?", (2.0,)))
```

`PostgresClient(PostgresConfig(...))`, `OracleClient(OracleConfig(...))`, and
`SqlServerClient(SqlServerConfig(...))` expose the same basic methods. All configuration
classes are frozen dataclasses with keyword-only parameters. Passwords are excluded from
their representations. Constructors validate without connecting. Database context entry
does not connect; the first operation does. `close()` is idempotent and terminal.

`query(sql, params=None)` returns dictionary rows. Column names retain driver casing;
use distinct aliases when selecting duplicate column names. `execute()` and `executemany()`
return native affected-row counts, including `-1` when the driver cannot determine one.
Keep native parameter binding: PostgreSQL/SQL Server `%s` or named `%(name)s`, Oracle
`:name`, SQLite `?` or `:name`. Never interpolate user values or identifiers into SQL.

Standalone operations commit on success and roll back on failure. `transaction()` yields
a session bound to one connection, commits on successful exit, and rolls back when an
exception escapes the block. Catch operation errors outside that block when all-or-nothing
behavior is required. A session is invalid after exit. Database DDL follows native behavior:
Oracle DDL implicitly commits and cannot be made transactional by this wrapper.

Failures raise `ConnectionError` or `OperationError`, with the driver exception in
`__cause__`. Empty query results mean a successful query with zero rows. A failed commit
can have an uncertain outcome; do not retry writes blindly. No SQL operations are retried.
Wrapper error text excludes SQL, bind values and credentials; driver exception chains and
application log messages can still contain sensitive data.

### Connection ownership

- PostgreSQL opens per-operation connections by default. Set `pool=True` for a native
  Psycopg pool, created on first use. Pool bounds and acquisition timeout are configurable;
  exhaustion raises an error. Returned connections commit or roll back before reuse.
- Oracle opens per-operation connections by default; `pool=True` uses its native pool with
  a timed acquisition. Thin mode is the default. Call `init_oracle_thick(lib_dir=...)`
  explicitly before any Oracle connections to use installed Oracle Client libraries.
  Thick/Thin selection is process-wide; old servers may require Thick mode.
- SQL Server opens per-operation connections; no custom pooling. Its encryption setting
  defaults to `require`. Certificate verification and trust settings are controlled by
  FreeTDS configuration; encryption alone is not a certificate trust policy. Native
  pymssql/FreeTDS timeout settings can affect the process; prefer consistent timeouts.
- SQLite retains one lazy connection, including for `:memory:`, and serializes operations
  across calling threads. Nested transactions are rejected; use the active session.

Share a client instance, not a session or cursor, between application modules. The optional
named registry provides `init_database(name, client)`, `get_database(name)`,
`close_database(name)`, and `close_all_databases()`, all importable from `industrial_connect.db`.
For example, `oracle = init_database("production", OracleClient(config))` returns that
same client; any file can call `get_database("production").query(...)` without passing
objects through handlers. See the [named database examples](../examples/README.md).
Close registered clients through the registry after workers finish. Ordinary unregistered
clients can still be used independently. Do not share clients across processes or close pooled clients while
operations are in flight. PostgreSQL defaults to `sslmode="verify-full"`; configure
`sslrootcert` for your CA. Plaintext connections require explicit configuration.
Oracle accepts native TCPS/wallet options in its DSN; configure them for your deployment.

### Oracle PL/SQL

```python
# client is an OracleClient configured by the application
import oracledb

with client.transaction() as tx:
    result = tx.call(
        "begin :total := :input + 1; open :rows for select :input as value from dual; end;",
        {"input": 41},
        {"total": oracledb.DB_TYPE_NUMBER, "rows": oracledb.DB_TYPE_CURSOR},
    )
    print(result.out_binds, result.cursors, result.implicit_results)
```

Output types accept native constants (`oracledb.DB_TYPE_NUMBER`, `oracledb.DB_TYPE_VARCHAR`,
`oracledb.DB_TYPE_CURSOR`, etc.), Python types (`int`, `str`, etc.), and legacy string names.
Type hints cover native Oracle types and Oracle transaction sessions. Shared database
methods expose `QueryParams`, `Row`, affected-row counts, and typed context-manager results.
Optional drivers are imported for type checking only until a connection is used.
Output REF CURSORs and
implicit results are fully fetched before cursor cleanup. Client-level `call()` commits
on success; session-level `call()` participates in its surrounding transaction. There is
no detached `commit()` method that might accidentally use another connection.
