"""python -m examples.env_config [--env-file examples/.env.example]"""

import argparse

from industrial_connect.config import load_env
from industrial_connect.db import SqliteClient, SqliteConfig, close_database, init_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", help="Explicit .env path; otherwise use process variables only"
    )
    args = parser.parse_args()
    settings = load_env(args.env_file)
    config = SqliteConfig(database=settings.get("SQLITE_DATABASE") or "store.db")
    database = init_database("local", SqliteClient(config))
    try:
        print(database.query("SELECT 1 AS value"))
    finally:
        close_database("local")


if __name__ == "__main__":
    main()
