"""节点文件能力只追加四张表；回滚旧应用时保留新表和审计证据。"""
import argparse
from sqlalchemy import inspect
from .config import Settings
from .db import database, initialize
from .models import Gate

TABLES = {"node_file_config", "node_file_requests", "task_file_options", "run_file_options"}


def check(engine, factory) -> bool:
    tables = set(inspect(engine).get_table_names())
    if "system_gate" not in tables:
        return False
    present = TABLES.issubset(tables)
    if present:
        indexes = {index["name"]: index["column_names"] for index in inspect(engine).get_indexes("run_file_options")}
        present = indexes.get("ix_run_file_options_retry") == ["node_id", "retry_requested"]
    with factory() as session:
        gate = session.get(Gate, 1)
        return bool(present and gate and gate.schema_version == 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="只追加节点文件能力表，不改旧表与schema_version")
    parser.add_argument("action", choices=["check", "apply"])
    args = parser.parse_args()
    settings = Settings()
    engine, factory = database(settings)
    try:
        if args.action == "apply":
            initialize(engine, factory)
        if not check(engine, factory):
            raise SystemExit("节点文件能力表未就绪；请在已备份的部署维护窗口显式执行apply")
        print("节点文件能力四表就绪，既有schema_version=1保持")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
