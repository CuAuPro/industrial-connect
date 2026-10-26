"""Read equipment temperatures; setup and sample inserts require explicit CLI flags."""

from datetime import UTC, datetime
from uuid import uuid4

from industrial_connect.db import OracleClient, OracleConfig

from ._common import parser, run


def main() -> None:
    args = parser(__doc__, database=True).parse_args()
    config = OracleConfig.from_env(env_file=args.env_file)
    with OracleClient(config) as db:
        if args.initialize:
            db.execute("""CREATE TABLE ic_example_readings (
                sample_id VARCHAR2(36) PRIMARY KEY, equipment_id VARCHAR2(64) NOT NULL,
                observed_at TIMESTAMP WITH TIME ZONE NOT NULL, temperature_c BINARY_DOUBLE NOT NULL)""")
        if args.record:
            observed = datetime.now(UTC)
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO ic_example_readings "
                    "(sample_id, equipment_id, observed_at, temperature_c) VALUES (:1, :2, "
                    "TO_TIMESTAMP_TZ(:3, 'YYYY-MM-DD\"T\"HH24:MI:SS.FF6TZH:TZM'), :4)",
                    (
                        str(uuid4()),
                        args.equipment,
                        observed.isoformat(timespec="microseconds"),
                        args.temperature,
                    ),
                )
        rows = db.query(
            "SELECT equipment_id, observed_at, temperature_c FROM ic_example_readings "
            "WHERE equipment_id = :1 ORDER BY observed_at DESC FETCH FIRST 10 ROWS ONLY",
            (args.equipment,),
        )
        for row in rows:
            print(row)


if __name__ == "__main__":
    run(main)
