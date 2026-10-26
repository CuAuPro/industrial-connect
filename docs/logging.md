# Logging

Configure logging once in your application's entry point. Do not pass logger objects
to connectors or between modules:

```python
from pathlib import Path
from industrial_connect.logging import setup_logging

Path("logs").mkdir(exist_ok=True)
setup_logging(
    "equipment-collector",
    file="logs/collector.log",
    graylog_host="graylog.internal",  # Omit if Graylog is not used.
)
```

In any module, including modules imported before startup:

```python
from industrial_connect.logging import get_logger

logger = get_logger(__name__)


def record_reading(equipment_id, temperature_c):
    logger.info(
        "Temperature recorded for %s", equipment_id,
        extra={"equipment_id": equipment_id, "temperature_c": temperature_c},
    )
```

`get_logger()` returns a standard Python logger. Without a name it uses
`industrial_connect`; `get_logger(__name__)` preserves the source module name.
`logging.getLogger(__name__)` works identically. There is no logger argument in
`record_reading()` or in connector constructors. Connectors use their own module
loggers for connection/pool state and safe error summaries. SQL, bind values,
passwords, raw payloads and driver exception chains are not included in those
summaries. Per-connection database acquisition messages use DEBUG.

## Output formats

| Destination | Format |
| --- | --- |
| Console | Existing readable timestamp, level, module and message |
| Rotating UTF-8 file | The same readable text |
| Graylog | Native GELF JSON with timestamp, severity, application and module fields |

Graylog uses `app_name` as its GELF facility, with the module in `_logger`.
`extra={"equipment_id": ..., "temperature_c": ...}` becomes structured GELF fields,
retaining JSON numbers and booleans. Do not `json.dumps()` a whole log event yourself.
The Graylog handler does not receive the console formatter, so the JSON message has
no duplicate timestamp or severity prefix. The driver may compress/chunk GELF UDP
packets. See [Graylog transport](graylog.md).

## Ownership and shutdown

`setup_logging()` explicitly configures the process-wide root logger and level so
application and library modules share the same outputs. Call it once per process;
a later call replaces the outputs created by this helper. It still returns a named
logger for callers that want the return value, but assigning it is optional.

Only this helper's handlers are replaced and closed. Other root handlers are
preserved and can still emit independently. Module loggers propagate to the root;
custom handlers or disabled propagation on an individual logger retain their normal
Python behavior. Imports alone never configure the root or create files/connections.

At application shutdown, stop the connector/worker threads first, then call:

```python
from industrial_connect.logging import shutdown_logging
shutdown_logging()
```

This closes/removes only the helper's outputs; it leaves other handlers alone.
Configure files with `file=...`; no log file is created when `file` is omitted.
Parent directories must exist. Rotation defaults to 10 MB with three backups;
use `max_bytes` and `backup_count` to change it. Default level is INFO.

The root configuration also applies to third-party loggers. Standard per-module
levels remain available, for example `get_logger("asyncua").setLevel("WARNING")`
to reduce verbose driver logs. Avoid logging credentials, raw connection strings
and native exception chains in application code.

Run [the console/file example](../examples/logging_setup.py):

```shell
python -m examples.logging_setup --file logs/collector.log
```

It configures logging in startup and uses a module logger, with 5 MB rotation and
five backups. Install `industrial-connect[graylog]` for Graylog output; console/files
need no optional driver.
