# Disposable integration tests

These tests create and drop dedicated test tables. Use only disposable services, never
production endpoints. All Compose ports bind to loopback. No service runs by default.

For the in-process OPC UA tests, install `.[all,dev]` and set `IC_TEST_SERVICES=opcua`:

```powershell
$env:IC_TEST_SERVICES = 'opcua'
python -m pytest tests/integration -q
```

On Linux use `IC_TEST_SERVICES=opcua python -m pytest tests/integration -q`.
These tests cover a real unsecured round trip, a pinned encrypted connection, and
disconnect/reconnect subscription recovery. No external server is needed.

For other services, Docker must have a running Linux engine. Set a strong disposable
password that meets SQL Server requirements, and start only the services you need:

```powershell
$env:IC_TEST_PASSWORD = '<your-strong-disposable-password>'
docker compose -p industrial-connect-test -f tests/integration/compose.yaml up -d
$env:IC_TEST_SERVICES = 'postgres,oracle,mssql,questdb,mqtt,opcua'
python -m pytest tests/integration -q
docker compose -p industrial-connect-test -f tests/integration/compose.yaml down -v
```

Wait for PostgreSQL/Oracle health and the SQL Server/QuestDB startup logs before running
tests. `IC_TEST_HOST` defaults to `127.0.0.1`; ports are 15432 PostgreSQL, 11521 Oracle,
11433 SQL Server, 19000 QuestDB QWP/WebSocket, and 11883 MQTT. Passwords must
match the Compose environment. Oracle uses appuser/FREEPDB1, PostgreSQL appuser/industrial,
SQL Server sa/master, QuestDB HTTP/QWP user admin. Insecure transport and anonymous MQTT here are
explicit test-only configurations.

QuestDB is pinned to 10.1.0 for QWP. Other Compose services use major/stable image tags for convenient disposable testing; capture image
digests when establishing your organization's supported server matrix. Oracle and SQL
Server containers need substantial memory; SQL Server uses its Developer edition under
its container EULA. No SQL Server container is started by the normal unit test or CI jobs.

The library does not retry writes. Tests may poll reads to observe eventual QuestDB WAL
visibility and OPC UA reconnect events.
