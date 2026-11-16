"""只执行恢复后的派发保护 SQL；不宣称完成 pg_restore 或容器恢复。"""
import importlib.util
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("restore_operations", ROOT / "deploy" / "operations.py")
operations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operations)


@pytest.fixture
def isolated_gate():
    filename = os.environ.get("WORKBENCH_TEST_DATABASE_FILE")
    if not filename:
        pytest.skip("未指定专用 PostgreSQL 测试连接；真实恢复保护 SQL 未验证")
    connection_url = json.loads(Path(filename).read_text(encoding="utf-8"))["database_url"]
    connection_url = connection_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(connection_url) as connection:
        schema = "restore_guard_" + uuid.uuid4().hex
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        statement = operations.DISABLE_RESTORED_DISPATCH_SQL.replace("public.system_gate", f"{schema}.system_gate")
        try:
            yield connection, schema, statement
        finally:
            # 测试 schema、表和数据都只在此未提交事务中存在，回滚不影响任何现存对象。
            connection.rollback()


def table(connection, schema):
    connection.execute(sql.SQL("CREATE TABLE {}.system_gate "
                               "(id integer, schema_version integer, dispatch_enabled boolean)").format(
                                   sql.Identifier(schema)))


def test_restore_guard_disables_previously_enabled_gate(isolated_gate):
    connection, schema, statement = isolated_gate
    table(connection, schema)
    connection.execute(sql.SQL("INSERT INTO {}.system_gate VALUES (1, 1, true)").format(sql.Identifier(schema)))
    connection.execute(statement)
    value = connection.execute(sql.SQL("SELECT dispatch_enabled FROM {}.system_gate").format(
        sql.Identifier(schema))).fetchone()[0]
    assert value is False
    connection.execute(statement)  # 已关闭时重复执行仍安全，不重新打开。


@pytest.mark.parametrize("rows", [[], [(1, 2, True)], [(2, 1, True)], [(1, 1, True), (2, 1, True)]])
def test_restore_guard_rejects_unknown_version_identity_or_row_count(isolated_gate, rows):
    connection, schema, statement = isolated_gate
    table(connection, schema)
    for row in rows:
        connection.execute(sql.SQL("INSERT INTO {}.system_gate VALUES (%s, %s, %s)").format(
            sql.Identifier(schema)), row)
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute(statement)


def test_restore_guard_rejects_missing_known_schema(isolated_gate):
    connection, schema, statement = isolated_gate
    with pytest.raises(psycopg.errors.UndefinedTable):
        connection.execute(statement)
