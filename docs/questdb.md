# QuestDB

Run [the equipment historian example](../examples/questdb.py) from the checkout:

```shell
python -m examples.questdb --env-file .env --initialize --record
python -m examples.questdb --env-file .env --record --equipment MILL_1 --temperature 73.2
python -m examples.questdb --env-file .env
```

Install `industrial-connect[questdb,env]`. Set `QUESTDB_CONF` to your QWP connection
string, for example `wss::addr=historian.example:9000;username=collector;password=...;auto_flush=off;`.
Use a test schema/account. Setup and inserts are opt-in. The example creates a WAL
table with deduplication keys `(timestamp, sample_id)` and UUID IDs; it never drops
it. Preserve both values when intentionally replaying a sample. It waits up to ten
seconds for acknowledged data to become visible without re-sending the reading.
Use a server supporting QWP ingress, egress and UUID values (the disposable test
configuration pins QuestDB 10.1.0).


One `QuestDbClient(QuestDbConfig(conf=...))` uses the official Python driver's QWP
WebSocket pool for ingestion and SQL. Use `wss::` for TLS; `ws::` explicitly selects
plaintext for local testing. Both the server and client must support QWP ingress and
egress. There is no automatic fallback to HTTP or PostgreSQL wire protocol.

```python
import time
from industrial_connect.db import QuestDbClient, QuestDbConfig, init_database

# Or QuestDbConfig.from_env(): reads QUESTDB_CONF.
with QuestDbClient(QuestDbConfig(
    conf="wss::addr=questdb.example:9000;auto_flush=off;",
)) as db:
    db.row("readings", symbols={"site": "line1"}, columns={"value": 1.5},
           timestamp_ns=time.time_ns())
    db.flush()
    print(db.query("select * from readings where site = $1 limit 5", ["line1"]))
    # Schema setup and administrative SQL are explicit application actions:
    # db.execute("create table ...")

# For shared cross-module use:
# db = init_database("historian", QuestDbClient(QuestDbConfig.from_env()))
```

Constructors and context entry do not import the optional driver or connect. The first
operation creates the official pool. The driver may warm connections at that point;
`lazy_connect=true` in the connection string enables its background connection behavior.
One client serializes its public operations and owns its sender lease and pool. Do not
share it across processes. `close()` is idempotent and terminal.

`row()` takes explicit integer Unix nanoseconds. `flush()` waits for acknowledgement
and checks the sender's server-rejection diagnostics; rejection or lost diagnostics
raise `OperationError`. A successful flush confirms acceptance, not immediate query
visibility: QuestDB WAL application is asynchronous. Query visibility may need polling.
`query(sql, params=None)` fetches dictionary rows without pandas or PyArrow. Use unique
column aliases. Results are fully materialized; timestamp values use the driver's UTC
Python datetimes, with nanosecond timestamps rounded down to microseconds. Use positional
`$1`, `$2` binds with a list or tuple; named dictionaries
are not accepted. `execute()` drains a SQL statement and returns the native affected-row
count, or `None` when no execution report exists. DDL may report zero. There is no
relational `transaction()` or `executemany()` API.

Normal context exit flushes and waits before releasing the sender and pool. Exceptional
exit discards rows still buffered on the lease; auto-flush or previous flushes may already
have published other rows. Cleanup cannot retract published data. The driver controls
its native buffering, retries and optional store-and-forward via the connection string;
the wrapper adds no retries or exactly-once guarantee. Delivery failures may have an
uncertain outcome: inspect native exception chains and use application/table deduplication
before replaying input. Wrapper messages omit SQL, binds and credentials; native driver
exceptions and logs may contain them.

Historian schemas, retention and application retry policy remain application-owned. See the
[official Python client guide](https://questdb.com/docs/connect/clients/python/).
