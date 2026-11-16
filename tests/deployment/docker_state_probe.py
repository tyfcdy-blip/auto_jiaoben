"""在显式隔离测试容器内只读比较持久状态；输出摘要，不输出数据或凭据。"""
import hashlib
import json
import os

from cryptography.fernet import Fernet
from sqlalchemy import select

from workbench.config import Settings
from workbench.db import database
from workbench.models import Artifact, Batch, BatchFile, Channel, Gate, LogEntry, Program, Run, Task, TriggerEvent, Version


def main() -> None:
    settings = Settings()
    if settings.env != 'development':
        raise ValueError('此测试探针仅用于隔离开发实例')
    engine, factory = database(settings)
    summary = {'uid': os.getuid(), 'collections': {}}
    try:
        with factory() as db:
            summary['dispatch_enabled'] = db.get(Gate, 1).dispatch_enabled
            for model in (Program, Version, Task, Batch, BatchFile, Run, TriggerEvent, LogEntry, Artifact, Channel):
                rows = db.execute(select(model.__table__).order_by(model.id)).mappings().all()
                encoded = json.dumps([dict(row) for row in rows], sort_keys=True, ensure_ascii=False).encode()
                summary['collections'][model.__tablename__] = {'count': len(rows), 'sha256': hashlib.sha256(encoded).hexdigest()}
            channels = db.scalars(select(Channel)).all()
            for channel in channels:
                decrypted = Fernet(settings.secret_key.encode()).decrypt(channel.encrypted_config.encode())
                assert isinstance(json.loads(decrypted), dict)
            summary['decryptable_channels'] = len(channels)
        objects = {}
        for path in sorted((settings.data_dir / 'objects').glob('*')):
            if path.is_file():
                objects[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert objects and all(name == digest for name, digest in objects.items())
        summary['objects'] = objects
        print(json.dumps(summary, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
