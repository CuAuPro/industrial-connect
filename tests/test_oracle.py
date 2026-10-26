from types import SimpleNamespace

import pytest
from test_databases import FakeConnection, FakeCursor

from industrial_connect.db.oracle import OracleClient, OracleConfig
from industrial_connect.errors import ConnectionError, OperationError


@pytest.mark.parametrize("native_types", [False, True])
def test_oracle_out_binds_ref_cursor_and_implicit_results(monkeypatch, native_types):
    import industrial_connect.db.oracle as module

    conn = FakeConnection()
    ref = FakeCursor(conn)
    implicit = FakeCursor(conn)
    native = pytest.importorskip("oracledb") if native_types else None
    cursor_type = native.DB_TYPE_CURSOR if native is not None else "cursor-type"
    number_type = native.DB_TYPE_NUMBER if native is not None else int
    seen_types = []

    class Cursor(FakeCursor):
        def var(self, type_):
            seen_types.append(type_)
            return SimpleNamespace(getvalue=lambda: ref if type_ == cursor_type else 123)

        def getimplicitresults(self):
            return [implicit]

    conn.cursor = lambda: Cursor(conn)
    driver = SimpleNamespace(DB_TYPE_CURSOR=cursor_type, connect=lambda **_: conn)
    monkeypatch.setattr(module, "require", lambda *_: driver)
    with OracleClient(OracleConfig(dsn="d", user="u", password="secret")) as client:
        with client.transaction() as session:
            result = session.call(
                "begin demo(:input, :total, :rows); end;",
                {"input": 1},
                {"total": number_type, "rows": cursor_type if native_types else "DB_TYPE_CURSOR"},
            )
            assert result.out_binds == {"total": 123}
            assert result.cursors == {"rows": [{"value": 42}]}
            assert result.implicit_results == [[{"value": 42}]]
            assert conn.commits == 0
        assert conn.commits == 1
        assert conn.cursor_closes == 3
        assert seen_types == [number_type, cursor_type]
        with pytest.raises(OperationError) as error:
            client.call("fail")
        assert isinstance(error.value.__cause__, ValueError)
        assert conn.commits == 1
        assert conn.rollbacks == 1


def test_oracle_pool_failure_does_not_fall_back(monkeypatch):
    import industrial_connect.db.oracle as module

    def fail(**_):
        raise ValueError("pool unavailable")

    driver = SimpleNamespace(create_pool=fail, POOL_GETMODE_TIMEDWAIT=1)
    monkeypatch.setattr(module, "require", lambda *_: driver)
    client = OracleClient(OracleConfig(dsn="d", user="u", password="s", pool=True))
    with pytest.raises(ConnectionError) as error:
        client.query("select")
    assert isinstance(error.value.__cause__, ValueError)
    assert client._pool is None
    client.close()


def test_oracle_pool_release_and_close(monkeypatch):
    import industrial_connect.db.oracle as module

    connection = FakeConnection()
    calls = []
    pool = SimpleNamespace(acquire=lambda: connection, close=lambda: calls.append("close"))

    def create_pool(**kwargs):
        assert kwargs["getmode"] == "timed"
        assert kwargs["wait_timeout"] == 10000
        calls.append("create")
        return pool

    driver = SimpleNamespace(create_pool=create_pool, POOL_GETMODE_TIMEDWAIT="timed")
    monkeypatch.setattr(module, "require", lambda *_: driver)
    client = OracleClient(OracleConfig(dsn="d", user="u", password="s", pool=True))
    assert calls == []
    client.query("select")
    assert connection.closes == 1
    client.close()
    client.close()
    assert calls == ["create", "close"]
