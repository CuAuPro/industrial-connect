# Application configuration and environment overrides

For applications with YAML/JSON, keep stable non-secret settings there: hosts, ports,
TLS/security, certificate paths, generation flags and timeouts. Load settings and
credentials once, then pass the selected values explicitly to each connector config.
A `.env` file is not required. Usernames may accompany passwords in the credential file;
QuestDB credentials are part of its native `QUESTDB_CONF` string.

The standalone examples intentionally use environment-only configuration and have no
YAML file. Their sample `.env` therefore includes required connection addresses, but
omits redundant defaults. This is not a second configuration catalog for IPE applications.

## Load once, configure explicitly

Install the optional file loader independently of connector drivers:

```shell
pip install "industrial-connect[env]"
```

`load_env()` returns a mapping. It reads only an explicitly supplied file, applies
process variables above file values, and never changes `os.environ`. Without a
file argument it reads process variables and needs no optional dependency.
Load once at application startup, then choose each connector's settings explicitly:

```python
from industrial_connect.config import load_env
from industrial_connect.db import PostgresConfig
from industrial_connect.mqtt import MqttConfig

settings = load_env(".env")  # Use load_env() for deployment-provided variables.
postgres = PostgresConfig(
    host=settings["PGHOST"],
    database=settings["PGDATABASE"],
    user=settings["PGUSER"],
    password=settings["PGPASSWORD"],
)
mqtt = MqttConfig(
    host=settings["MQTT_HOST"],
    username=settings.get("MQTT_USERNAME"),
    password=settings.get("MQTT_PASSWORD"),
)
```

The loader returns raw strings (or `None` for file keys without a value).
Validate required values and convert numeric/boolean values before passing them
to constructors. Settings can also come from application-owned YAML/JSON; direct
constructors do not read environment variables or apply hidden overrides.

## Per-connector convenience API

Every connector config also supports `from_env()` for scripts:

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
| `OpcUaConfig` | `OPCUA_` | `OPCUA_ENDPOINT`, `OPCUA_NAME`, `OPCUA_USERNAME`, `OPCUA_CERTIFICATE` |

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

## Structured application settings

`Config.from_mapping(settings)` accepts fields from a YAML/JSON dictionary and applies
process environment overrides above those values, then constructor defaults. Unknown
fields fail immediately. It performs no file discovery or process-environment mutation.
Use this when an application owns its configuration file; `from_env()` remains useful
for environment-only tools and explicitly supplied `.env` files.

```python
config = MqttConfig.from_mapping({"host": "localhost", "port": 1883, "tls": False})
```
