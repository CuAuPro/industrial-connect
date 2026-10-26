# Graylog

Install `industrial-connect[graylog]`. Configure an approved Graylog GELF/UDP input:

```shell
python -m examples.graylog --host graylog.internal --port 12201
```

Alternatively set `GRAYLOG_HOST` and `GRAYLOG_PORT`. [The example](../examples/graylog.py)
adds structured `equipment_id` and `temperature_c` fields, retains console output,
and closes the handlers it owns at shutdown. This only sends an event; it cannot
confirm that Graylog received or indexed it.

`setup_logging("collector", graylog_host=..., graylog_port=...)` uses graypy's
GELF/UDP handler. UDP provides neither delivery acknowledgements nor TLS. Use an
appropriate protected network, and use your existing log shipping infrastructure
when authenticated encrypted or durable delivery is required. No credentials, raw
connection configurations or driver exception chains belong in log messages.

Configure the outputs once at startup and use `get_logger(__name__)` in every
module. Read [logging configuration and ownership](logging.md). The helper attaches
its handlers to the root logger, preserving handlers installed by other code. Console
and file output stay readable text; the Graylog handler produces GELF JSON with
`app_name` as the facility and structured `extra` fields. Call `shutdown_logging()`
after application workers stop to close only the helper's outputs.
