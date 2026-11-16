import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import httpx

from .config import AgentError, Config
from .connection import import_connection
from .runner import Agent
from .storage import StateLock


def main() -> int:
    parser = argparse.ArgumentParser(description="自动化工作台原生开发执行器")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path)
    mode.add_argument("--import-connection", type=Path, help="导入下载的节点连接文件，不联网或启动执行")
    parser.add_argument("--connection-dir", type=Path, help="导入的全新目录，默认当前用户目录下按节点保存")
    parser.add_argument("--once", action="store_true", help="最多领取一次任务后退出，适用于本地验证")
    arguments = parser.parse_args()
    agent = None
    try:
        if arguments.import_connection:
            if arguments.once:
                raise AgentError("导入不会执行任务；请导入后使用 --config 启动")
            path = import_connection(arguments.import_connection, arguments.connection_dir)
            command = [sys.executable, "-m", "agent", "--config", str(path)]
            print(f"连接配置已导入：{path}")
            print("下一步，在当前项目目录启动执行器：")
            print(subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command))
            print("尚未连接控制台。请妥善删除下载的连接文件；导入目录含节点凭据，请勿分享。")
            return 0
        if arguments.connection_dir:
            raise AgentError("--connection-dir 仅用于导入连接文件")
        config_path = arguments.config.resolve()
        config = Config.load(config_path)
        agent = Agent(config, config_path=config_path)
        process_lock = Path.home() / ".automation-workbench" / "agent-process.lock"
        process_lock.parent.mkdir(parents=True, exist_ok=True)
        with StateLock(process_lock):
            while True:
                try:
                    agent.run_once()
                except httpx.HTTPError:
                    print("控制台暂不可达或拒绝请求；本地状态保留，等待恢复", file=sys.stderr)
                    if arguments.once:
                        return 2
                if arguments.once:
                    return 0
                time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        return 130
    except (AgentError, OSError, ValueError, TypeError, KeyError) as error:
        # 不输出 HTTP 请求头、配置原文或任何令牌内容。
        reason = str(error) if isinstance(error, AgentError) else type(error).__name__
        print(f"执行器启动或协议失败：{reason}", file=sys.stderr)
        return 2
    finally:
        if agent:
            agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
