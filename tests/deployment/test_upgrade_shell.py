"""完整 Shell 包装器隔离演练，公网 Nginx 探针明确不在本地验收范围。"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid

from cryptography.fernet import Fernet
import pytest

ROOT = Path(__file__).resolve().parents[2]


def command(arguments, content=None, expected=0):
    result = subprocess.run(arguments, input=content, capture_output=True, timeout=300)
    assert result.returncode == expected, result.stderr.decode(errors='replace')[-2000:]
    return result.stdout


def test_real_upgrade_and_rollback_shell(tmp_path):
    if os.environ.get('WB_UPGRADE_SHELL_TEST') != '1':
        pytest.skip('需显式启用完整隔离 Docker Shell 演练')
    suffix = uuid.uuid4().hex[:10]
    project, control, postgres = f'wb-shell-{suffix}', f'wb-control-{suffix}', f'wb-pg-{suffix}'
    # Docker Desktop 的 /tmp 只有约512MiB；用独占的磁盘目录实测1GiB备份余量门禁。
    docker_root = command(['docker', 'info', '--format', '{{.DockerRootDir}}']).decode().strip()
    hostroot = f'{docker_root}/tmp/wb-shell-{suffix}'
    old_image = 'automation-workbench:0.6.0-rc.4'
    new_image = os.environ.get('WB_UPGRADE_NEW_IMAGE', 'automation-workbench:node-files-local-check')
    dockerfile = f'''FROM docker:26-cli AS cli
FROM {new_image}
USER 0:0
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=cli /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
ENTRYPOINT ["sleep", "infinity"]
'''
    (tmp_path / 'Dockerfile').write_text(dockerfile, encoding='utf-8', newline='\n')
    command(['docker', 'build', '-t', 'wb-upgrade-test-control', str(tmp_path)])
    image = json.loads(command(['docker', 'image', 'inspect', new_image]))[0]
    labels = image['Config']['Labels']
    app = tmp_path / 'app'
    secret_dir = app / 'secrets'
    secret_dir.mkdir(parents=True)
    (app / 'state').mkdir()
    delivery = tmp_path / 'delivery'
    scripts, payload = delivery / 'scripts', delivery / 'payload'
    scripts.mkdir(parents=True)
    payload.mkdir()
    password = secrets.token_urlsafe(24)
    for name, value in {'database_password': password,
        'database_url': f'postgresql+psycopg://workbench:{password}@{postgres}:5432/automation_workbench',
        'encryption_key': Fernet.generate_key().decode(), 'monitor_token': secrets.token_urlsafe(32),
        'admin_password': secrets.token_urlsafe(32)}.items():
        (secret_dir / name).write_text(value, encoding='utf-8')
    environment = {'WB_COMPOSE_PROJECT': project, 'WB_IMAGE': old_image, 'WB_ENV': 'test',
        'WB_DISPATCH_ENABLED': 'false', 'WB_BASE_PATH': '/automation', 'WB_PORT': '0',
        'WB_DATABASE_NETWORK': project, 'WB_INGRESS_NETWORK': project}
    for key, name in [('DATABASE_PASSWORD', 'database_password'), ('DATABASE_URL', 'database_url'),
                      ('ENCRYPTION_KEY', 'encryption_key'), ('MONITOR_TOKEN', 'monitor_token'),
                      ('ADMIN_PASSWORD', 'admin_password')]:
        environment[f'WB_{key}_SECRET_FILE'] = f'{hostroot}/app/secrets/{name}'
    (app / '.env.production').write_text(''.join(f'{key}={value}\n' for key, value in environment.items()), encoding='utf-8')
    for filename in ('compose.yaml', 'compose.external-db.yaml', 'compose.shared-nginx.yaml'):
        (app / filename).write_text((ROOT / 'deploy' / filename).read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    for filename in ('common.sh', 'upgrade-common.sh', 'upgrade.sh', 'rollback-upgrade.sh'):
        source = (ROOT / 'deploy/server' / filename).read_text(encoding='utf-8')
        if filename == 'common.sh':
            replacements = {'RELEASE_VERSION': labels['org.opencontainers.image.version'],
                'RELEASE_COMMIT': labels['org.opencontainers.image.revision'], 'RELEASE_IMAGE': new_image,
                'RELEASE_IMAGE_CONFIG_DIGEST': image['Id'], 'COMPOSE_PROJECT': project,
                'APP_DIR': hostroot + '/app', 'DATA_DIR': hostroot + '/data', 'DB_CONTAINER': postgres}
            import re
            for key, value in replacements.items():
                source = re.sub(rf'^readonly {key}=.*$', f'readonly {key}="{value}"', source, flags=re.M)
        (scripts / filename).write_text(source, encoding='utf-8', newline='\n')
    # 使用真实 API readiness 和组件检查，排除无法在隔离环境访问的生产公网边界。
    (scripts / 'verify.sh').write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
compose exec -T api python -c '
import json, pathlib, urllib.request
base="http://127.0.0.1:8000/automation/api/v1/health/"
assert json.load(urllib.request.urlopen(base+"ready"))["status"] == "ready"
token=pathlib.Path("/run/secrets/monitor_token").read_text().strip()
request=urllib.request.Request(base+"components",headers={"Authorization":"Bearer "+token})
assert json.load(urllib.request.urlopen(request))["status"] == "healthy"
'
''', encoding='utf-8', newline='\n')
    archive = payload / f'automation-workbench_{labels["org.opencontainers.image.version"]}_linux-amd64.tar'
    command(['docker', 'save', '-o', str(archive), new_image])
    import hashlib
    checksum_files = [file for file in delivery.rglob('*') if file.is_file()]
    (delivery / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.relative_to(delivery).as_posix()}\n'
        for file in checksum_files), encoding='utf-8', newline='\n')
    command(['docker', 'network', 'create', project])
    try:
        command(['docker', 'run', '-d', '--name', postgres, '--network', project,
            '-e', 'POSTGRES_USER=workbench', '-e', 'POSTGRES_DB=automation_workbench',
            '-e', 'POSTGRES_PASSWORD=' + password, '--tmpfs', '/var/lib/postgresql/data', 'postgres:17-bookworm'])
        command(['docker', 'exec', postgres, 'sh', '-c',
            'for i in $(seq 1 60); do pg_isready -U workbench -d automation_workbench && exit 0; sleep 1; done; exit 1'])
        command(['docker', 'run', '-d', '--name', control, '-v', '/var/run/docker.sock:/var/run/docker.sock',
                 '-v', f'{hostroot}:{hostroot}', 'wb-upgrade-test-control'])
        command(['docker', 'cp', str(app), f'{control}:{hostroot}/app'])
        command(['docker', 'cp', str(delivery), f'{control}:{hostroot}/delivery'])

        def shell(source, expected=0):
            return command(['docker', 'exec', control, 'bash', '-c', source], expected=expected)

        common = f'source {hostroot}/delivery/scripts/common.sh\n'
        helper = f'source {hostroot}/delivery/scripts/upgrade-common.sh\n'
        shell(helper + 'require_backup_space 1073741824 0 0 0 0\n')
        shell(helper + 'require_backup_space 1073741823 0 0 0 0\n', expected=1)
        shell(helper + 'require_backup_space invalid 0 0 0 0\n', expected=1)
        shell(helper + f'verify_upgrade_image {new_image} sha256:wrong version revision\n', expected=1)
        shell(common + 'compose run --rm --no-deps init-db\ncompose up -d --no-build --no-deps api worker\n')
        ready = shell(common + 'sleep 3\ncompose ps -a\ncompose logs --tail 30 api worker\n')
        assert b'Application startup complete' in ready, ready.decode(errors='replace')
        output = shell(f'printf "UPGRADE\\n" | bash {hostroot}/delivery/scripts/upgrade.sh\n')
        assert '升级完成'.encode() in output
        snapshot = shell(f'cat {hostroot}/app/state/latest-upgrade-backup').decode().strip()
        output = shell(f'printf "ROLLBACK\\n" | bash {hostroot}/delivery/scripts/rollback-upgrade.sh {snapshot}\n')
        assert '镜像已回滚'.encode() in output
        shell(f'test -f {hostroot}/app/state/rollback-dispatch-blocked\n')
        result = command(['docker', 'exec', postgres, 'psql', '-U', 'workbench', '-d',
                          'automation_workbench', '-Atc', 'SELECT dispatch_enabled FROM system_gate;'])
        assert result.strip() == b'f'
        # 仅故障边界注入数据库不可达；恢复动作真实停止隔离 API 和 Worker。
        shell(helper + 'upgrade_sql() { return 1; }; trap upgrade_failure ERR; false\n', expected=1)
        assert shell(common + 'compose ps -q --status running api worker\n').strip() == b''
        # 停止服务也失败时不得宣称成功：输出必须要求人工隔离。
        failure = subprocess.run(['docker', 'exec', control, 'bash', '-c', helper +
            'upgrade_sql() { return 1; }; compose() { return 1; }; trap upgrade_failure ERR; false\n'],
            capture_output=True, timeout=30)
        assert failure.returncode == 1 and '无法确认派发关闭'.encode() in failure.stderr
    finally:
        # 仅清理本用例唯一项目的容器和卷，绝不使用全局 prune。
        containers = command(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']).decode().split()
        for container in containers:
            command(['docker', 'rm', '-f', container])
        # 只清理本用例随机目录内的合成数据；挂载点本身由 Docker 保留。
        subprocess.run(['docker', 'exec', control, 'find', hostroot, '-mindepth', '1', '-delete'],
                       capture_output=True, timeout=30)
        for name in (control, postgres):
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        volumes = command(['docker', 'volume', 'ls', '-q', '--filter', f'label=com.docker.compose.project={project}']).decode().split()
        for volume in volumes:
            command(['docker', 'volume', 'rm', volume])
        networks = command(['docker', 'network', 'ls', '-q', '--filter', f'label=com.docker.compose.project={project}']).decode().split()
        for network in networks:
            command(['docker', 'network', 'rm', network])
        command(['docker', 'network', 'rm', project])
