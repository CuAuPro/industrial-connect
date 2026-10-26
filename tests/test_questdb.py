from collections import deque
from types import SimpleNamespace

import pytest

from industrial_connect.db import QuestDbClient, QuestDbConfig
from industrial_connect.errors import ConnectionError, DependencyError, OperationError


class FakeResult:
    def __init__(self):
        self.closed = False
        self.fail = False

    def columns(self):
        return [("value", "LONG"), ("nullable", "VARCHAR")]

    def iter_rows(self):
        yield (42, None)
        if self.fail:
            raise TimeoutError("private query details")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    import industrial_connect.db.questdb as module

    calls = []
    errors = deque()
    result = FakeResult()
    sender = SimpleNamespace(
        row=lambda *a, **k: calls.append(("row", a, k)),
        flush=lambda **k: calls.append(("flush", k)),
        close=lambda **k: calls.append(("sender_close", k)),
        poll_error=lambda: errors.popleft() if errors else None,
        error_events_dropped=lambda: 0,
    )
    db = SimpleNamespace(
        sender=lambda: calls.append("sender") or sender,
        query=lambda *a: calls.append(("query", a)) or result,
        execute=lambda *a: calls.append(("execute", a)) or SimpleNamespace(rows_affected=3),
        close=lambda **kwargs: calls.append("pool_close"),
    )
    driver = SimpleNamespace(
        connect=lambda conf: calls.append(("connect", conf)) or db,
        TimestampNanos=int,
    )
    monkeypatch.setattr(module, "require", lambda *_: driver)
    return SimpleNamespace(calls=calls, errors=errors, result=result, sender=sender, db=db)


def client():
    return QuestDbClient(QuestDbConfig(conf="wss::addr=localhost:9000;auto_flush=off;"))


def add_row(db):
    db.row("readings", symbols={"site": "one"}, columns={"value": 1}, timestamp_ns=123)


def test_lazy_shared_pool_ingestion_query_execute_and_cleanup(fake):
    db = client()
    with db:
        assert fake.calls == []
        db.flush()
        assert fake.calls == []
        add_row(db)
        assert fake.calls[-1] == (
            "row",
            ("readings",),
            {"symbols": {"site": "one"}, "columns": {"value": 1}, "at": 123},
        )
        db.flush()
        assert fake.calls[-1] == ("flush", {"wait": True})
        assert db.query("select $1", [42]) == [{"value": 42, "nullable": None}]
        assert fake.calls[-1] == ("query", ("select $1", [42]))
        assert fake.result.closed
        assert db.execute("update readings set value=$1", (3,)) == 3
        assert fake.calls[-1] == ("execute", ("update readings set value=$1", [3]))
        assert sum(isinstance(c, tuple) and c[0] == "connect" for c in fake.calls) == 1
    assert fake.calls[-3:] == [
        ("flush", {"wait": True}),
        ("sender_close", {"flush": False}),
        "pool_close",
    ]
    before = list(fake.calls)
    db.close()
    assert fake.calls == before
    for operation in [
        lambda: add_row(db),
        db.flush,
        lambda: db.query("select 1"),
        lambda: db.execute("drop table readings"),
        db.__enter__,
    ]:
        with pytest.raises(ConnectionError, match="closed"):
            operation()


def test_query_failure_closes_result_and_keeps_driver_cause(fake):
    fake.result.fail = True
    db = client()
    with pytest.raises(OperationError, match="query failed") as error:
        db.query("private SQL")
    assert isinstance(error.value.__cause__, TimeoutError)
    assert "private" not in str(error.value)
    assert fake.result.closed
    db.close()


def test_empty_query_and_statement_without_report(fake):
    fake.result.iter_rows = lambda: iter(())
    fake.db.execute = lambda *args: None
    with client() as db:
        assert db.query("select value from readings") == []
        assert fake.result.closed
        assert db.execute("select 1") is None


def test_pool_initialization_failure_is_chained_and_retryable(monkeypatch):
    import industrial_connect.db.questdb as module

    def fail(conf):
        raise TimeoutError("private credentials")

    monkeypatch.setattr(module, "require", lambda *_: SimpleNamespace(connect=fail))
    db = client()
    with pytest.raises(ConnectionError, match="initialize") as error:
        db.query("select 1")
    assert isinstance(error.value.__cause__, TimeoutError)
    assert db._db is None
    assert "private" not in str(error.value)
    db.close()


@pytest.mark.parametrize("kind", ["flush", "row", "execute", "sender"])
def test_driver_failure_is_chained_without_wrapper_retry(fake, kind):
    db = client()
    if kind == "flush":
        add_row(db)
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError("private details")

    target = fake.db if kind in {"sender", "execute"} else fake.sender
    setattr(target, kind, fail)
    operation = {
        "flush": db.flush,
        "row": lambda: add_row(db),
        "sender": lambda: add_row(db),
        "execute": lambda: db.execute("private SQL"),
    }[kind]
    with pytest.raises(OperationError) as error:
        operation()
    assert isinstance(error.value.__cause__, TimeoutError)
    assert "private" not in str(error.value)
    assert attempts == [1]
    db.close(flush=False)


@pytest.mark.parametrize("dropped", [False, True])
def test_flush_surfaces_server_rejections_and_lost_diagnostics(fake, dropped):
    db = client()
    add_row(db)
    if dropped:
        fake.sender.error_events_dropped = lambda: 1
    else:
        fake.errors.extend([object(), object()])
    with pytest.raises(OperationError, match="ingestion errors"):
        db.flush()
    assert not fake.errors
    db.close(flush=False)


def test_exceptional_exit_discards_only_unpublished_rows(fake):
    with pytest.raises(RuntimeError):
        with client() as db:
            add_row(db)
            raise RuntimeError("cancel")
    assert not any(isinstance(c, tuple) and c[0] == "flush" for c in fake.calls)
    assert fake.calls[-2:] == [("sender_close", {"flush": False}), "pool_close"]


def test_failed_close_flush_still_releases_resources_without_second_flush(fake):
    db = client()
    add_row(db)

    def fail(**kwargs):
        fake.calls.append("failed_flush")
        raise TimeoutError("uncertain")

    fake.sender.flush = fail
    with pytest.raises(OperationError, match="uncertain"):
        db.close()
    assert fake.calls[-3:] == ["failed_flush", ("sender_close", {"flush": False}), "pool_close"]
    db.close()


def test_sender_close_failure_still_closes_pool(fake):
    db = client()
    add_row(db)

    def fail(**kwargs):
        raise RuntimeError("cleanup")

    fake.sender.close = fail
    with pytest.raises(ConnectionError, match="resources"):
        db.close(flush=False)
    assert fake.calls[-1] == "pool_close"
    fake.sender.close = lambda **kwargs: None
    db.close()
    assert db._sender is None and db._db is None
    with pytest.raises(ConnectionError, match="closed"):
        db.query("select 1")


@pytest.mark.parametrize(
    "conf",
    ["http::addr=localhost:9000;", "https::addr=localhost:9000;", "tcp::addr=localhost:9009;", ""],
)
def test_only_explicit_qwp_configuration_is_accepted(conf):
    with pytest.raises(ValueError, match="QWP"):
        QuestDbConfig(conf=conf)


@pytest.mark.parametrize("params", [{"value": 1}, "secret", b"secret"])
def test_invalid_bind_parameters_fail_before_connect(fake, params):
    db = client()
    for operation in (db.query, db.execute):
        with pytest.raises(ValueError, match="positional"):
            operation("select $1", params)
    assert not fake.calls
    db.close()


def test_real_qwp_configuration_without_network():
    driver = pytest.importorskip("questdb")
    handle = driver.connect("wss::addr=localhost:9000;auto_flush=off;lazy_connect=true;")
    handle.close()


def test_missing_driver_error_is_preserved(monkeypatch):
    import industrial_connect.db.questdb as module

    def missing(*args):
        raise DependencyError("pip install industrial-connect[questdb]")

    monkeypatch.setattr(module, "require", missing)
    with pytest.raises(DependencyError, match="questdb"):
        client().query("select 1")
