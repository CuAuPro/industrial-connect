# Environment variables or .env files

Every connector config supports `from_env()`. Direct constructors still work:

```python
from industrial_connect.db import OracleClient, OracleConfig, init_database
from industrial_connect.mqtt import MqttClient, MqttConfig
from industrial_connect.opcua import OpcUaClient, OpcUaConfig

oracle = init_database("production", OracleClient(OracleConfig.from_env(env_file=".env")))
mqtt = MqttClient(MqttConfig.from_env(env_file=".env"))
opcua = OpcUaClient(OpcUaConfig.from_env(env_file=".env"))

# Process variables only: no dotenv dependency required.
config = OracleConfig.from_env()

# Custom prefix for another Oracle database; explicit arguments override env/file values.
reporting = OracleConfig.from_env(env_file=".env", prefix="REPORTING_ORACLE_", pool=True)

# Plain programmatic settings remain supported.
local = OracleConfig(dsn="localhost/service", user="app", password="example")
```

Keys are the prefix plus the **uppercase config field name**:

| Config | Default prefix | Example key |
| --- | --- | --- |
| `OracleConfig` | `ORACLE_` | `ORACLE_DSN` |
| `PostgresConfig` | `PG` | `PGHOST`, `PGDATABASE`, `PGPOOL` |
| `SqlServerConfig` | `MSSQL_` | `MSSQL_SERVER` |
| `SqliteConfig` | `SQLITE_` | `SQLITE_DATABASE` |
| `QuestDbConfig` | `QUESTDB_` | `QUESTDB_CONF` |
| `MqttConfig` | `MQTT_` | `MQTT_HOST`, `MQTT_CLIENT_ID`, `MQTT_USERNAME` |
| `OpcUaConfig` | `OPCUA_` | `OPCUA_ENDPOINT`, `OPCUA_USERNAME`, `OPCUA_CERTIFICATE` |

QuestDB ingestion and SQL share `QuestDbConfig.from_env(env_file=".env")`;
set `QUESTDB_CONF` to a `wss::` connection string (`ws::` for explicit local plaintext).

Precedence is explicit arguments, process environment, .env values, then config defaults.
Numbers and booleans are parsed (`true/false`, `1/0`, `yes/no`, `on/off`, case-insensitive).
Empty optional values become `None`; required string values remain strings. Missing
required fields and invalid values raise errors identifying variable names without values.
The usual config validation still applies. No .env discovery or `os.environ` mutation occurs;
python-dotenv handles file quoting and interpolation. Reading an explicitly supplied file
requires the `env` extra, also included in `all`.

See [the sample file](../examples/.env.example). A service-free example is runnable with:

```shell
python -m examples.env_config --env-file examples/.env.example
```
