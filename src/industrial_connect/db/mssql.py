from dataclasses import dataclass, field

from .._optional import require
from ..config import EnvConfig
from ..logging import get_logger
from ._base import DatabaseClient

logger = get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class SqlServerConfig(EnvConfig):
    _env_prefix = "MSSQL_"

    server: str
    database: str
    user: str
    password: str = field(repr=False)
    port: int = 1433
    login_timeout: int = 10
    query_timeout: int = 30
    encryption: str = "require"

    def __post_init__(self):
        self._validate_numbers()
        if not self.server or not self.database or not self.user:
            raise ValueError("server, database and user are required")
        if not 0 < self.port < 65536 or min(self.login_timeout, self.query_timeout) <= 0:
            raise ValueError("Invalid port or timeout")
        if self.encryption not in {"off", "request", "require"}:
            raise ValueError("Invalid SQL Server encryption setting")


class SqlServerClient(DatabaseClient):
    config: SqlServerConfig

    def __init__(self, config: SqlServerConfig) -> None:
        super().__init__(config)

    def _connect(self):
        logger.debug("Opening SQL Server connection")
        c = self.config
        return require("pymssql", "mssql").connect(
            server=c.server,
            port=str(c.port),
            user=c.user,
            password=c.password,
            database=c.database,
            login_timeout=c.login_timeout,
            timeout=c.query_timeout,
            encryption=c.encryption,
            as_dict=False,
            autocommit=False,
        )
