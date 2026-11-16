"""在明确的本机隔离工作台生成目录测试项目，保留测试任务供交互复验。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.connection import import_connection  # noqa: E402


def check_response(response: httpx.Response, status: int = 200) -> dict:
    if response.status_code != status:
        raise AssertionError(f"合成测试接口未通过：{response.status_code}，预期{status}")
    return response.json()


def file_hashes(directory: Path) -> dict:
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


def run_acceptance(session_file: Path, output: Path) -> None:
    metadata = json.loads(session_file.read_text(encoding='utf-8'))
    if urlsplit(metadata['url']).hostname != '127.0.0.1' or not (
            metadata.get('schema', '').startswith('integration_')
            or metadata.get('schema') == 'docker-isolated-workbench-dev'):
        raise ValueError('仅允许明确选择的本机隔离联调实例')
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'runtime') or output.exists():
        raise ValueError('测试输出必须位于本工作区runtime中的全新目录')
    node_executable = shutil.which('node')
    if not node_executable:
        raise ValueError('本机未找到Node.js运行时')
    output.mkdir(parents=True)
    inputs, packages = output / 'inputs', output / 'packages'
    subprocess.run([sys.executable, str(ROOT / 'examples/folder-jobs/prepare.py'), '--input-dir', str(inputs),
                    '--bundle-dir', str(packages)], check=True, cwd=ROOT)
    (inputs / 'empty').mkdir()
    outside = output / 'outside'
    outside.mkdir()
    (outside / 'not-input.txt').write_text('synthetic outside root', encoding='utf-8')
    original_hashes = file_hashes(inputs)
    stamp = uuid.uuid4().hex[:8]
    evidence = {'url': metadata['url'], 'project_name': '文件夹输入验收-' + stamp, 'checks': [], 'tasks': {}, 'runs': {}}
    schedule_id = None
    started = time.monotonic()
    with httpx.Client(base_url=metadata['url'].rstrip('/') + '/api/v1/', timeout=15, follow_redirects=False, trust_env=False) as client:
        login = check_response(client.post('auth/login', json={'username': metadata['username'], 'password': metadata['password']}))
        client.headers['Authorization'] = 'Bearer ' + login['token']
        project = check_response(client.post('projects', json={'name': evidence['project_name']}), 201)
        evidence['project_id'] = project['id']
        node = check_response(client.post('nodes', json={'project_id': project['id'], 'name': '当前电脑 · Python与Node测试',
                              'capabilities': ['python', 'node', 'directory-input-v1'], 'slots': 1,
                              'input_roots': [str(inputs)]}), 201)
        connection_file = output / 'connection.local.json'
        connection_file.write_text(json.dumps({'format': 'workbench-connection-v1', 'node_id': node['id'],
             'name': node['name'], 'base_url': metadata['url'], 'token': node['token'],
             'runtimes': {'python': sys.executable, 'node': node_executable}, 'capabilities': ['python', 'node'],
             'input_roots': [str(inputs)]}), encoding='utf-8')
        try:
            config_file = import_connection(connection_file, output / 'connection')
        finally:
            connection_file.unlink(missing_ok=True)
        evidence['agent_config'] = str(config_file)
        evidence['input_dir'] = str(inputs)
        definitions = json.loads((packages / 'programs.json').read_text(encoding='utf-8'))
        task_defaults = {}
        for definition in definitions:
            slug = Path(definition['zip']).stem
            program = check_response(client.post('programs', json={'project_id': project['id'], 'name': definition['name']}), 201)
            schema = definition['parameter_schema']
            schema['x-workbench-input'] = 'directory'
            version = check_response(client.post(f'programs/{program["id"]}/versions', json={
                'name': 'synthetic-v1', 'command': definition['command'], 'parameter_schema': schema,
                'side_effects': False, 'result_required': True, 'required_capabilities': definition['required_capabilities']}), 201)
            check_response(client.post(f'versions/{version["id"]}/bundle', content=(packages / definition['zip']).read_bytes(),
                                       headers={'Content-Type': 'application/zip'}))
            check_response(client.post(f'versions/{version["id"]}/publish'))
            defaults = definition['example_parameters']
            task = check_response(client.post('tasks', json={'project_id': project['id'], 'name': definition['name'],
                                  'version_id': version['id'], 'node_id': node['id'], 'default_parameters': defaults,
                                  'timeout_seconds': definition['suggested_timeout_seconds']}), 201)
            assert task['input_mode'] == 'directory' and task['business_key_fields'] == ['batch_key']
            evidence['tasks'][slug] = task['id']
            task_defaults[slug] = defaults
        caller = check_response(client.post('callers', json={'project_id': project['id'], 'name': '合成影刀上游',
                                'task_ids': list(evidence['tasks'].values())}), 201)
        caller_headers = {'Authorization': 'Bearer ' + caller['token']}

        def record(label: str) -> None:
            evidence['checks'].append(label)
            print('PASS ' + label, flush=True)

        def submit(slug: str, suffix: str, directory: Path = inputs) -> str:
            response = check_response(client.post(f'tasks/{evidence["tasks"][slug]}/runs', headers=caller_headers,
                json={'event_id': f'{stamp}-{suffix}', 'parameters': {**task_defaults[slug],
                      'input_dir': str(directory), 'batch_key': f'{stamp}-{suffix}'}}), 202)
            evidence['runs'][suffix] = response['run_id']
            return response['run_id']

        def execute(run_id: str, expected: str) -> dict:
            process = subprocess.run([sys.executable, '-m', 'agent', '--config', str(config_file), '--once'],
                                     cwd=ROOT, capture_output=True, timeout=35)
            assert process.returncode == 0, '执行器未正常返回，查看本地运行证据'
            detail = check_response(client.get(f'runs/{run_id}'))
            assert detail['state'] == expected, f'运行终态为{detail["state"]}，预期{expected}'
            return detail

        try:
            csv_id = submit('csv-merge', 'csv')
            csv_run = execute(csv_id, 'succeeded')
            assert csv_run['result']['metrics']['rows'] == 3
            artifact = csv_run['artifacts'][0]
            content = client.get(f'runs/{csv_id}/artifacts/{artifact["id"]}')
            assert content.status_code == 200 and 'SYNTHETIC-C' in content.text
            record('Python递归目录CSV合并及真实产物下载')
            duplicate = check_response(client.post(f'tasks/{evidence["tasks"]["csv-merge"]}/runs', headers=caller_headers,
                json={'event_id': f'{stamp}-csv', 'parameters': {**task_defaults['csv-merge'], 'input_dir': str(inputs), 'batch_key': f'{stamp}-csv'}}))
            assert duplicate['run_id'] == csv_id and duplicate['deduplicated']
            record('影刀式事件携带目录参数，重复请求不重复执行')
            node_run = execute(submit('node-summary', 'node'), 'succeeded')
            assert node_run['result']['metrics']['files'] == 4
            record('Node.js读取同一目录并统计四个文件')
            failed = execute(submit('fail', 'fail'), 'failed')
            assert failed['exit_code'] == 7 and failed['result']['status'] == 'failed'
            logs = check_response(client.get(f'runs/{failed["id"]}/logs'))
            assert logs['entries']
            record('主动报错、非零退出和日志回传')
            timeout_run = execute(submit('wait', 'timeout'), 'failed')
            assert timeout_run['observation'] == 'timeout'
            record('真实进程超时终止并回传失败状态')
            for name, folder in [('missing', inputs / 'missing'), ('empty', inputs / 'empty'), ('outside', outside)]:
                failure = execute(submit('csv-merge', name, folder), 'failed')
                assert failure['observation'] == 'startup_failed' and failure['started_at'] is None
                record(f'目录{name}在业务程序启动前被拒绝')
            alerts = check_response(client.get('alerts', params={'project_id': project['id'], 'limit': 200}))
            assert {failed['id'], timeout_run['id']}.issubset({item['run_id'] for item in alerts['items']})
            record('失败和超时生成工作台告警，不发送真实钉钉')
            schedule = check_response(client.post('schedules', json={'task_id': evidence['tasks']['csv-merge'],
                'name': '每分钟目录测试（验收后关闭）', 'cron': '* * * * *', 'timezone': 'Asia/Shanghai',
                'parameters': {'input_dir': str(inputs), 'batch_key': '${scheduled_date}'}, 'enabled': True}), 201)
            schedule_id = schedule['id']
            deadline = time.monotonic() + 80
            scheduled_run = None
            while time.monotonic() < deadline:
                rows = check_response(client.get('runs', params={'project_id': project['id'], 'limit': 200}))['items']
                scheduled_run = next((row for row in rows if row['source'] == 'schedule'), None)
                if scheduled_run:
                    break
                time.sleep(1)
            assert scheduled_run, '定时任务未在限定时间内真实触发'
            check_response(client.patch(f'schedules/{schedule_id}', json={'enabled': False}))
            scheduled_run = execute(scheduled_run['id'], 'succeeded')
            assert scheduled_run['parameters']['batch_key'] == datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')
            evidence['runs']['schedule'] = scheduled_run['id']
            record('真实时钟定时触发目录任务，业务日期参数解析成功')
            assert file_hashes(inputs) == original_hashes
            record('所有处理完成后，原始输入文件内容保持不变')
            evidence.update(passed=len(evidence['checks']), failed=0, elapsed_seconds=round(time.monotonic() - started, 2))
        finally:
            if schedule_id:
                check_response(client.patch(f'schedules/{schedule_id}', json={'enabled': False}))
            check_response(client.patch(f'callers/{caller["id"]}', json={'active': False}))
            (output / 'acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'验收证据与可重测任务位于：{output}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    run_acceptance(arguments.session_file, arguments.output)
