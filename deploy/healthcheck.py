"""仅探测当前容器就绪状态，不输出可能包含敏感信息的响应体。"""

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    base = os.environ.get("WB_BASE_PATH", "").rstrip("/")
    try:
        with urlopen(f"http://127.0.0.1:8000{base}/api/v1/health/ready", timeout=3) as response:
            return 0 if response.status == 200 else 1
    except (HTTPError, URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
