"""等待执行器建立进程树约束后再启动业务进程，不读取执行器配置。"""

import json
import subprocess
import sys


def main() -> int:
    command = json.loads(sys.stdin.buffer.readline())
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return 125
    return subprocess.run(command, stdin=subprocess.DEVNULL, shell=False, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
