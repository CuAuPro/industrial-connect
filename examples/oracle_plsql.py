from industrial_connect.db import OracleClient, OracleConfig

from ._common import parser, run


def main():
    args = parser("Calculate a value and fetch a REF CURSOR in one transaction").parse_args()
    import oracledb

    config = OracleConfig.from_env(env_file=args.env_file)
    with OracleClient(config) as client:
        with client.transaction() as session:
            result = session.call(
                "begin :total := :value + 1; open :rows for select :value as value from dual; end;",
                {"value": 41},
                {"total": oracledb.DB_TYPE_NUMBER, "rows": oracledb.DB_TYPE_CURSOR},
            )
            print(result.out_binds, result.cursors)


if __name__ == "__main__":
    run(main)
