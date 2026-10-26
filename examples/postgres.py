"""Read equipment temperatures; setup and sample inserts require explicit CLI flags."""

from datetime import UTC, datetime
from uuid import uuid4

from industrial_connect.db import PostgresClient, PostgresConfig

from ._common import parser, run


def main() -> None:
    args = parser(__doc__, database=True).parse_args()
    config = PostgresConfig.from_env(env_file=args.env_file)
    with PostgresClient(config) as db:
        if args.initialize:
            db.execute("""CREATE TABLE IF NOT EXISTS ic_example_readings (
                sample_id VARCHAR(36) PRIMARY KEY, equipment_id VARCHAR(64) NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL, temperature_c DOUBLE PRECISION NOT NULL)""")
        if args.record:
            observed = datetime.now(UTC)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO ic_example_readings "
                    "(sample_id, equipment_id, observed_at, temperature_c) VALUES (%s, %s, %s, %s)",
                    (str(uuid4()), args.equipment, observed, args.temperature),
                )
        rows = db.query(
            "SELECT equipment_id, observed_at, temperature_c FROM ic_example_readings "
            "WHERE equipment_id = %s ORDER BY observed_at DESC LIMIT 10",
            (args.equipment,),
        )
        for row in rows:
            print(row)


if __name__ == "__main__":
    run(main)
