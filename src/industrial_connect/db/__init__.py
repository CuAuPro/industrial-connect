"""Database clients. Optional drivers load only when a connection is opened."""

from .mssql import SqlServerClient, SqlServerConfig
from .oracle import OracleClient, OracleConfig, OracleResult, OracleSession, init_oracle_thick
from .postgres import PostgresClient, PostgresConfig
from .questdb import QuestDbClient, QuestDbConfig
from .registry import close_all_databases, close_database, get_database, init_database
from .sqlite import SqliteClient, SqliteConfig
from .types import QueryParams, Row

__all__ = [
    "PostgresClient",
    "PostgresConfig",
    "OracleClient",
    "OracleConfig",
    "OracleResult",
    "init_oracle_thick",
    "SqlServerClient",
    "SqlServerConfig",
    "SqliteClient",
    "SqliteConfig",
    "QuestDbClient",
    "QuestDbConfig",
    "OracleSession",
    "QueryParams",
    "Row",
    "close_all_databases",
    "close_database",
    "get_database",
    "init_database",
]
