"""Read equipment temperatures; setup and sample inserts require explicit CLI flags."""

from datetime import UTC, datetime
from uuid import uuid4

from industrial_connect.db import SqlServerClient, SqlServerConfig

from ._common import parser, run


def main() -> None:
    args = parser(__doc__, database=True).parse_args()
    config = SqlServerConfig.from_env(env_file=args.env_file)
    with SqlServerClient(config) as db:
        if args.initialize:
            db.execute("""IF OBJECT_ID(N'ic_example_readings', N'U') IS NULL
                CREATE TABLE ic_example_readings (
                sample_id VARCHAR(36) PRIMARY KEY, equipment_id VARCHAR(64) NOT NULL,
                observed_at DATETIME2 NOT NULL, temperature_c FLOAT NOT NULL)""")
        if args.record:
            observed = datetime.now(UTC)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO ic_example_readings "
                    "(sample_id, equipment_id, observed_at, temperature_c) VALUES (%s, %s, %s, %s)",
                    (str(uuid4()), args.equipment, observed.replace(tzinfo=None), args.temperature),
                )
        rows = db.query(
            "SELECT equipment_id, observed_at, temperature_c FROM ic_example_readings "
            "WHERE equipment_id = %s ORDER BY observed_at DESC OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
            (args.equipment,),
        )
        for row in rows:
            print(row)


if __name__ == "__main__":
    run(main)
