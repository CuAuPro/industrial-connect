"""Standard-library-only wheel and optional-dependency isolation check."""

import importlib.metadata
import importlib.util
import sys
import tempfile
from pathlib import Path

from industrial_connect import __version__
from industrial_connect.db import (
    SqliteClient,
    SqliteConfig,
    close_all_databases,
    get_database,
    init_database,
)


def main():
    extra = sys.argv[1] if len(sys.argv) > 1 else "core"
    expected = {
        "core": set(),
        "sqlite": set(),
        "env": {"dotenv"},
        "postgres": {"psycopg", "psycopg_pool"},
        "oracle": {"oracledb"},
        "mssql": {"pymssql"},
        "questdb": {"questdb"},
        "opcua": {"asyncua"},
        "mqtt": {"paho"},
        "graylog": {"graypy"},
    }
    expected["db"] = set.union(*(expected[k] for k in ("postgres", "oracle", "mssql", "questdb")))
    expected["all"] = (
        expected["db"]
        | expected["opcua"]
        | expected["mqtt"]
        | expected["graylog"]
        | expected["env"]
    )
    optional = expected["all"]
    installed = {name for name in optional if importlib.util.find_spec(name) is not None}
    assert installed == expected[extra], (extra, installed, expected[extra])
    assert not optional & sys.modules.keys(), "Package imports loaded an optional driver"
    assert SqliteConfig.from_env(prefix="IC_INSTALL_CHECK_").database == "store.db"
    assert "dotenv" not in sys.modules, "Environment-only configuration imported dotenv"
    if "dotenv" in expected[extra]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("IC_INSTALL_CHECK_DATABASE=:memory:\n", encoding="utf-8")
            assert (
                SqliteConfig.from_env(env_file=path, prefix="IC_INSTALL_CHECK_").database
                == ":memory:"
            )
    with SqliteClient(SqliteConfig(database=":memory:")) as client:
        assert client.query("select 1 as value") == [{"value": 1}]
    try:
        client = init_database("install-check", SqliteClient(SqliteConfig(database=":memory:")))
        assert get_database("install-check") is client
        assert get_database("install-check").query("select 1 as value") == [{"value": 1}]
    finally:
        close_all_databases()
    distribution = importlib.metadata.distribution("industrial-connect")
    assert distribution.version == __version__
    print(f"{extra}: wheel imports, optional isolation, and SQLite passed")


if __name__ == "__main__":
    main()
