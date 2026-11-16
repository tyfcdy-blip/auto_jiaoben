"""独立审查复用正式隔离测试 fixture，不访问集成环境。"""

import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location("review_backend_fixtures", Path(__file__).parents[1] / "backend" / "conftest.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)

app = _fixtures.app
client = _fixtures.client
admin = _fixtures.admin
project = _fixtures.project
registered = _fixtures.registered
