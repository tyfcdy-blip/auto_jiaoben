"""隔离 Docker 演练核心升级/回滚操作；不连接生产，不修改正在运行的预览。"""
import os
from pathlib import Path
import re
import secrets
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]


def docker(*arguments: str, content: bytes | None = None) -> bytes:
    result = subprocess.run(['docker', *arguments], input=content, capture_output=True, timeout=120)
    if result.returncode:
        # 参数可包含隔离环境密码，不在错误输出中打印命令。
        pytest.fail('隔离 Docker 操作失败：' + result.stderr.decode(errors='replace')[-1500:])
    return result.stdout


def test_upgrade_and_rollback_core_with_real_postgres(tmp_path):
    if os.environ.get('WB_UPGRADE_DOCKER_TEST') != '1':
        pytest.skip('需显式启用隔离 Docker 升级演练')
    old_image = os.environ.get('WB_UPGRADE_OLD_IMAGE', 'automation-workbench:0.6.0-rc.4')
    new_image = os.environ.get('WB_UPGRADE_NEW_IMAGE', 'automation-workbench:node-files-local-check')
    suffix = uuid.uuid4().hex[:12]
    network, postgres = f'wb-upgrade-{suffix}', f'wb-upgrade-pg-{suffix}'
    password = secrets.token_urlsafe(24)
    docker('network', 'create', network)
    try:
        docker('run', '-d', '--name', postgres, '--network', network,
               '-e', 'POSTGRES_DB=automation_workbench', '-e', 'POSTGRES_USER=workbench',
               '-e', 'POSTGRES_PASSWORD=' + password, '--tmpfs', '/var/lib/postgresql/data',
               'postgres:17-bookworm')
        docker('exec', postgres, 'sh', '-c',
               'for i in $(seq 1 60); do pg_isready -U workbench -d automation_workbench && exit 0; sleep 1; done; exit 1')
        database = f'postgresql+psycopg://workbench:{password}@{postgres}:5432/automation_workbench'

        def application(image: str, source: str) -> str:
            return docker('run', '--rm', '-i', '--network', network, '-e', 'WB_ENV=test',
                          '-e', 'WB_DATABASE_URL=' + database, '--tmpfs', '/app/data:uid=10001,gid=10001',
                          '--entrypoint', 'python', image, '-', content=source.encode()).decode()

        def sql(statement: str) -> str:
            return docker('exec', '-i', postgres, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                          '-U', 'workbench', '-d', 'automation_workbench',
                          content=statement.encode()).decode()

        application(old_image, '''
from workbench.config import Settings
from workbench.db import database, initialize
from workbench.models import User
engine, factory = database(Settings())
initialize(engine, factory)
with factory.begin() as db:
    db.add(User(username='upgrade-synthetic', display_name='keep', password_hash='synthetic-hash', is_admin=True))
engine.dispose()
''')
        assert 'node_file_config' not in sql("SELECT tablename FROM pg_tables WHERE schemaname='public';")
        source = (ROOT / 'deploy/server/upgrade-common.sh').read_text(encoding='utf-8')
        guards = re.findall(r"upgrade_sql <<'SQL'\n(.*?)\nSQL", source, re.S)
        assert len(guards) == 2
        # 故障门禁用独立 schema 验证，真实生产 SQL 不允许活跃/未知状态混过去。
        sql('CREATE SCHEMA guard_test; CREATE TABLE guard_test.runs (state text);')
        check_guard = guards[1].replace('public.', 'guard_test.')
        for state in ('leased', 'running', 'unknown'):
            sql(f"TRUNCATE guard_test.runs; INSERT INTO guard_test.runs VALUES ('{state}');")
            rejected = subprocess.run(['docker', 'exec', '-i', postgres, 'psql', '-X', '-v',
                'ON_ERROR_STOP=1', '-U', 'workbench', '-d', 'automation_workbench'],
                input=check_guard.encode(), capture_output=True, timeout=30)
            assert rejected.returncode != 0 and b'active or unknown runs' in rejected.stderr
        sql("TRUNCATE guard_test.runs; INSERT INTO guard_test.runs VALUES ('queued'), ('waiting_input'), ('partial'), ('canceled');")
        sql(check_guard)
        sql('DROP SCHEMA guard_test CASCADE;')
        sql(guards[0])
        sql(guards[1])
        assert 'f' in sql('SELECT dispatch_enabled FROM system_gate;')
        backup = docker('exec', postgres, 'pg_dump', '-U', 'workbench', '-d', 'automation_workbench', '-Fc')
        assert backup.startswith(b'PGDMP')
        (tmp_path / 'database.dump').write_bytes(backup)
        docker('exec', '-i', postgres, 'pg_restore', '--list', content=backup)
        application(new_image, '''
from workbench.config import Settings
from workbench.db import database, initialize
from workbench.node_files_migration import check
engine, factory = database(Settings())
initialize(engine, factory)
initialize(engine, factory)
assert check(engine, factory)
engine.dispose()
''')
        assert 'node_file_config' in sql("SELECT tablename FROM pg_tables WHERE schemaname='public';")
        assert 'synthetic-hash' in sql("SELECT password_hash FROM users WHERE username='upgrade-synthetic';")
        assert 'f' in sql('SELECT dispatch_enabled FROM system_gate;')
        # 镜像回滚只验证旧应用可读且不能派发；不删除新表，不回灌业务库。
        application(old_image, '''
from workbench.config import Settings
from workbench.db import database
from workbench.models import Gate, User
from sqlalchemy import select
engine, factory = database(Settings())
with factory() as db:
    assert db.get(Gate, 1).dispatch_enabled is False
    assert db.scalar(select(User).where(User.username=='upgrade-synthetic')).password_hash == 'synthetic-hash'
engine.dispose()
''')
        # 真实恢复演练只导入另一个临时库，证明备份可恢复，不覆盖升级库。
        docker('exec', postgres, 'createdb', '-U', 'workbench', 'restore_check')
        docker('exec', '-i', postgres, 'pg_restore', '-U', 'workbench', '-d', 'restore_check',
               '--exit-on-error', content=backup)
        restored = docker('exec', postgres, 'psql', '-U', 'workbench', '-d', 'restore_check', '-Atc',
                          "SELECT password_hash FROM users WHERE username='upgrade-synthetic'").decode().strip()
        assert restored == 'synthetic-hash'
    finally:
        docker('rm', '-f', postgres)
        docker('network', 'rm', network)
