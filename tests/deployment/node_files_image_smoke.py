"""在新镜像内使用临时 SQLite 检查启动、子路径及文件协议，不连接外部系统。"""

import json
from pathlib import Path
import re
import tempfile

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from workbench.config import Settings
from workbench.db import initialize
from workbench.main import create_app
from workbench.node_files_migration import check


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='node-files-image-') as temporary:
        directory = Path(temporary)
        settings = Settings(env='test', database_url='sqlite:///' + str(directory / 'test.db'),
                            data_dir=directory / 'storage', base_path='/automation',
                            secret_key=Fernet.generate_key().decode())
        app = create_app(settings)
        initialize(app.state.engine, app.state.factory)
        initialize(app.state.engine, app.state.factory)
        assert check(app.state.engine, app.state.factory)
        with TestClient(app) as client:
            index = client.get('/automation/')
            assert index.status_code == 200
            assets = re.findall(r'(?:src|href)="(/automation/assets/[^\"]+)"', index.text)
            assert len(assets) >= 2
            for asset in assets:
                assert client.get(asset).status_code == 200
            assert client.get('/automation/api/v1/nodes').status_code == 401
            assert client.get('/automation/api/openapi.json').status_code == 200
        for module in ['filesystem_guard.py', 'node_files.py', 'node_file_service.py']:
            assert (Path('/app/agent') / module).is_file()
        contract = json.loads(Path('/app/contracts/runtime/file-request.schema.json').read_text())
        assert contract['properties']['expires_at']['type'] == 'number'
    print('image runtime checks passed: additive tables, subpath page/assets/API, agent modules, Unix time contract')


if __name__ == '__main__':
    main()
