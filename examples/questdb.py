"""Record equipment readings over QWP and wait for bounded WAL query visibility."""

import time
from uuid import uuid4

from industrial_connect import OperationError
from industrial_connect.db import QuestDbClient, QuestDbConfig

from ._common import parser, run


def main() -> None:
    args = parser(__doc__, database=True).parse_args()
    with QuestDbClient(QuestDbConfig.from_env(env_file=args.env_file)) as db:
        if args.initialize:
            db.execute("""CREATE TABLE IF NOT EXISTS ic_example_readings (
                timestamp TIMESTAMP, sample_id UUID, equipment_id SYMBOL, temperature_c DOUBLE
                ) TIMESTAMP(timestamp) PARTITION BY DAY WAL
                DEDUP UPSERT KEYS(timestamp, sample_id)""")
        if args.record:
            sample_id = uuid4()
            observed_ns = time.time_ns()
            db.row(
                "ic_example_readings",
                symbols={"equipment_id": args.equipment},
                columns={"sample_id": sample_id, "temperature_c": args.temperature},
                timestamp_ns=observed_ns,
            )
            db.flush()
            # Acceptance and query visibility are different barriers. Do not replay the write.
            deadline = time.monotonic() + 10
            while not db.query(
                "SELECT sample_id FROM ic_example_readings WHERE sample_id=$1 LIMIT 1", [sample_id]
            ):
                if time.monotonic() >= deadline:
                    raise OperationError("Reading was acknowledged but query visibility timed out")
                time.sleep(0.1)
        for row in db.query(
            "SELECT timestamp, equipment_id, temperature_c FROM ic_example_readings "
            "WHERE equipment_id=$1 ORDER BY timestamp DESC LIMIT 10",
            [args.equipment],
        ):
            print(row)


if __name__ == "__main__":
    run(main)
