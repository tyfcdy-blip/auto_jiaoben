from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from workbench import cli
from workbench.config import Settings
from workbench.db import database


class FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    def dispose(self):
        self.dispose_calls += 1


class SqlStateError(RuntimeError):
    def __init__(self, sqlstate):
        super().__init__("synthetic database error")
        self.sqlstate = sqlstate


def test_database_initialization_retries_only_connection_failures(monkeypatch):
    attempts = []
    sleeps = []

    def initialize(_engine, _factory):
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise OperationalError("SELECT 1", {}, RuntimeError("synthetic unavailable"))

    monkeypatch.setattr(cli, "initialize", initialize)
    settings = SimpleNamespace(
        database_connect_attempts=3,
        database_connect_interval_seconds=0.25,
    )
    engine = FakeEngine()

    cli.initialize_database(settings, engine, object(), sleeper=sleeps.append)

    assert attempts == [1, 2, 3]
    assert sleeps == [0.25, 0.25]
    assert engine.dispose_calls == 2


def test_database_initialization_does_not_retry_schema_error(monkeypatch):
    sleeps = []

    def initialize(_engine, _factory):
        raise ValueError("synthetic incompatible schema")

    monkeypatch.setattr(cli, "initialize", initialize)
    settings = SimpleNamespace(
        database_connect_attempts=30,
        database_connect_interval_seconds=2,
    )
    engine = FakeEngine()

    with pytest.raises(ValueError, match="incompatible schema"):
        cli.initialize_database(settings, engine, object(), sleeper=sleeps.append)

    assert sleeps == []
    assert engine.dispose_calls == 0


def test_database_initialization_does_not_retry_bad_credentials(monkeypatch):
    sleeps = []

    def initialize(_engine, _factory):
        raise OperationalError("connect", {}, SqlStateError("28P01"))

    monkeypatch.setattr(cli, "initialize", initialize)
    settings = SimpleNamespace(
        database_connect_attempts=30,
        database_connect_interval_seconds=2,
    )
    engine = FakeEngine()

    with pytest.raises(OperationalError):
        cli.initialize_database(settings, engine, object(), sleeper=sleeps.append)

    assert sleeps == []
    assert engine.dispose_calls == 0


def test_database_initialization_fails_after_bounded_attempts(monkeypatch):
    def initialize(_engine, _factory):
        raise OperationalError("SELECT 1", {}, RuntimeError("synthetic unavailable"))

    monkeypatch.setattr(cli, "initialize", initialize)
    settings = SimpleNamespace(
        database_connect_attempts=2,
        database_connect_interval_seconds=0,
    )
    engine = FakeEngine()

    with pytest.raises(RuntimeError, match="2次连接尝试"):
        cli.initialize_database(settings, engine, object(), sleeper=lambda _: None)

    assert engine.dispose_calls == 2


def test_postgres_connection_pool_is_explicitly_bounded(tmp_path):
    settings = Settings(
        env="test",
        database_url="postgresql+psycopg://synthetic:synthetic@db:5432/synthetic",
        data_dir=tmp_path,
        database_pool_size=3,
        database_max_overflow=2,
        database_pool_timeout_seconds=7,
    )

    engine, _factory = database(settings)
    try:
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2
        assert engine.pool._timeout == 7
    finally:
        engine.dispose()
