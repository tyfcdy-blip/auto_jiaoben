import logging
import signal
import threading
from .config import Settings
from .db import database, lock
from .notifications import send_one
from .scheduling import scan_health, scan_schedules
from .retention import cleanup_files

logger = logging.getLogger(__name__)


def tick(factory, settings):
    with factory.begin() as db:
        lock(db)
        scan_schedules(db, settings)
        scan_health(db, settings)
    send_one(factory, settings)
    cleanup_files(factory, settings)


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    engine, factory = database(settings)
    stopping = threading.Event()
    def stop(_signum, _frame):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping.is_set():
            try:
                tick(factory, settings)
            except Exception as exc:
                # 每轮事务失败会回滚；保留错误类型，水位不推进，由外部监控发现停摆。
                logger.error("worker_iteration_failed exception_type=%s", type(exc).__name__)
            stopping.wait(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
