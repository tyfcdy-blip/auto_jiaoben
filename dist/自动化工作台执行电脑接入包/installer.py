"""Windows执行电脑一键接入；仅使用标准库启动安装流程。"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid


PACKAGE_VERSION = "0.1.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
NODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}")


class SetupError(Exception):
    """可直接显示给本机用户的接入错误。"""


def read_connection(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise SetupError("连接文件不存在、为链接或超过容量限制")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SetupError("连接文件无法读取或不是有效JSON") from error
    if (not isinstance(payload, dict) or payload.get("format") != "workbench-connection-v1"
            or not isinstance(payload.get("node_id"), str)
            or not NODE_PATTERN.fullmatch(payload["node_id"])
            or not isinstance(payload.get("runtimes"), dict) or not payload["runtimes"]):
        raise SetupError("这不是工作台生成的有效执行电脑连接文件")
    return payload


def confirm_connection(connection: dict) -> None:
    print("请核对本次接入信息：")
    print(f"  电脑名称：{connection.get('name', '未命名电脑')}")
    print(f"  工作台地址：{connection.get('base_url', '')}")
    for alias, executable in connection["runtimes"].items():
        print(f"  运行环境 {alias}：{executable}")
    roots = connection.get("input_roots") or []
    print("  允许读取目录：" + ("；".join(roots) if roots else "未配置"))
    answer = input("以上内容正确，并且连接文件是刚从工作台下载的吗？输入 YES 继续：").strip()
    if answer != "YES":
        raise SetupError("未确认连接信息，接入已取消")


def select_connection() -> Path:
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        selected = ""
    else:
        root = None
        try:
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askopenfilename(title="选择工作台连接文件",
                                                   filetypes=[("JSON连接文件", "*.json")])
        except tkinter.TclError:
            selected = ""
        finally:
            if root is not None:
                root.destroy()
    if not selected:
        raise SetupError("没有选择连接文件，接入已取消")
    return Path(selected).absolute()


def python_has_dependencies(executable: Path) -> bool:
    if not executable.is_file():
        return False
    check = subprocess.run([str(executable), "-I", "-c",
                            "import sys,httpx,jsonschema; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, check=False)
    return check.returncode == 0


def copy_package(destination: Path) -> None:
    for name in ("agent", "contracts"):
        source = PACKAGE_ROOT / name
        if not source.is_dir():
            raise SetupError("接入包不完整，请重新下载")
        shutil.copytree(source, destination / name)
    shutil.copy2(PACKAGE_ROOT / "requirements-agent.txt", destination / "requirements-agent.txt")
    (destination / "package-version.txt").write_text(PACKAGE_VERSION, encoding="utf-8")


def prepare_runtime(install_root: Path) -> Path:
    if install_root.is_symlink():
        raise SetupError("安装目录不能是链接")
    if install_root.exists():
        marker = install_root / "package-version.txt"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != PACKAGE_VERSION:
            raise SetupError("安装目录已存在其他版本，请联系管理员升级")
        candidates = [install_root / ".venv/Scripts/python.exe", Path(sys.executable)]
        for candidate in candidates:
            if python_has_dependencies(candidate):
                return candidate
        raise SetupError("已有接入环境不完整，请联系管理员修复")

    install_root.parent.mkdir(parents=True, exist_ok=True)
    staging = install_root.parent / f".agent-install-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        copy_package(staging)
        # 连接文件可被本机文件系统修改，因此安装阶段绝不执行其中声明的业务运行时。
        # 只信任用户用于启动本安装器的Python；缺少依赖时创建独立环境。
        candidates = [Path(sys.executable)]
        for candidate in candidates:
            if python_has_dependencies(candidate):
                runtime = candidate
                break
        else:
            environment = staging / ".venv"
            created = subprocess.run([sys.executable, "-m", "venv", str(environment)], timeout=120, check=False)
            if created.returncode != 0:
                raise SetupError("无法创建执行器独立Python环境")
            runtime = environment / "Scripts/python.exe"
            installed = subprocess.run([str(runtime), "-m", "pip", "install", "--disable-pip-version-check",
                                        "--no-input", "-r", str(staging / "requirements-agent.txt")],
                                       timeout=600, check=False)
            if installed.returncode != 0 or not python_has_dependencies(runtime):
                raise SetupError("执行器依赖安装失败，请检查网络后重试")
        os.replace(staging, install_root)
        return (install_root / runtime.relative_to(staging)) if runtime.is_relative_to(staging) else runtime
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def import_or_reuse(connection_file: Path, connection: dict, runtime: Path,
                    install_root: Path, connection_root: Path) -> Path:
    config = connection_root / "agent.json"
    if config.is_file():
        existing = json.loads(config.read_text(encoding="utf-8"))
        if existing.get("base_url") != connection.get("base_url"):
            raise SetupError("这台电脑已有同名节点，但工作台地址不同")
        return config
    if connection_root.exists():
        raise SetupError("节点连接目录已存在但不完整，请联系管理员核对")
    process = subprocess.run([str(runtime), "-m", "agent", "--import-connection", str(connection_file),
                              "--connection-dir", str(connection_root)], cwd=install_root,
                             stdin=subprocess.DEVNULL, timeout=60, check=False)
    if process.returncode != 0 or not config.is_file():
        raise SetupError("连接文件导入失败，请检查上方提示")
    return config


def background_files(runtime: Path, install_root: Path, config: Path,
                     startup_directory: Path) -> tuple[Path, Path]:
    connection_root = config.parent
    log = connection_root / "agent.log"
    wrapper = connection_root / "run-agent-background.py"
    wrapper.write_text(
        "import os,sys,threading\n"
        f"root={str(install_root)!r}\nconfig={str(config)!r}\nlog_path={str(log)!r}\n"
        "class RotatingText:\n"
        "    limit=5*1024*1024\n"
        "    def __init__(self,path):\n"
        "        self.path=path; self.backup=path+'.1'; self.lock=threading.Lock(); self._open()\n"
        "        self.size=os.path.getsize(self.path)\n"
        "    def _open(self): self.stream=open(self.path,'a',encoding='utf-8',buffering=1,newline='')\n"
        "    def _rotate(self):\n"
        "        self.stream.close()\n"
        "        try: os.unlink(self.backup)\n"
        "        except FileNotFoundError: pass\n"
        "        try: os.replace(self.path,self.backup)\n"
        "        except FileNotFoundError: pass\n"
        "        self._open(); self.size=0\n"
        "    @staticmethod\n"
        "    def _fitting_characters(text,byte_limit):\n"
        "        low=0; high=len(text)\n"
        "        while low<high:\n"
        "            middle=(low+high+1)//2\n"
        "            if len(text[:middle].encode('utf-8'))<=byte_limit: low=middle\n"
        "            else: high=middle-1\n"
        "        return low\n"
        "    def write(self,text):\n"
        "        if not isinstance(text,str): text=str(text)\n"
        "        original_length=len(text)\n"
        "        with self.lock:\n"
        "            while text:\n"
        "                if self.size>=self.limit: self._rotate()\n"
        "                count=self._fitting_characters(text,self.limit-self.size)\n"
        "                if count==0:\n"
        "                    self._rotate(); continue\n"
        "                part=text[:count]\n"
        "                self.stream.write(part)\n"
        "                self.size+=len(part.encode('utf-8'))\n"
        "                text=text[count:]\n"
        "            return original_length\n"
        "    def flush(self):\n"
        "        with self.lock: self.stream.flush()\n"
        "    def isatty(self): return False\n"
        "os.chdir(root)\nsys.path.insert(0,root)\n"
        "stream=RotatingText(log_path)\nsys.stdout=stream\nsys.stderr=stream\n"
        "sys.argv=['agent','--config',config]\nfrom agent.__main__ import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    pythonw = runtime.with_name("pythonw.exe")
    executable = pythonw if pythonw.is_file() else runtime
    command = f'"{executable}" "{wrapper}"'
    launcher = connection_root / "start-agent.vbs"
    escaped_root = str(install_root).replace('"', '""')
    escaped_command = command.replace('"', '""')
    launcher.write_text(
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{escaped_root}"\r\n'
        f'shell.Run "{escaped_command}", 0, False\r\n',
        encoding="utf-16",
    )
    startup_directory.mkdir(parents=True, exist_ok=True)
    startup = startup_directory / f"AutomationWorkbench-{config.parent.name}.vbs"
    temporary = startup.with_suffix(".tmp")
    shutil.copy2(launcher, temporary)
    os.replace(temporary, startup)
    return wrapper, startup


def start_agent(runtime: Path, wrapper: Path, install_root: Path) -> None:
    pythonw = runtime.with_name("pythonw.exe")
    executable = pythonw if pythonw.is_file() else runtime
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen([str(executable), str(wrapper)], cwd=install_root,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=flags, close_fds=True)
    time.sleep(2)
    if process.poll() is not None:
        raise SetupError(f"执行器没有保持运行，请查看日志：{wrapper.parent / 'agent.log'}")


def run(options: argparse.Namespace) -> None:
    if sys.version_info[:2] != (3, 12):
        raise SetupError("安装器需要Python 3.12")
    connection_file = options.connection.absolute() if options.connection else select_connection()
    connection = read_connection(connection_file)
    if not options.accept_config:
        confirm_connection(connection)
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    install_root = (options.install_root or local / "AutomationWorkbench/Agent/runtime-v0.1").absolute()
    connection_root = (options.connection_dir or Path.home() / ".automation-workbench/connections" /
                       connection["node_id"]).absolute()
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    startup = (options.startup_dir or appdata / "Microsoft/Windows/Start Menu/Programs/Startup").absolute()
    runtime = prepare_runtime(install_root)
    config = import_or_reuse(connection_file, connection, runtime, install_root, connection_root)
    wrapper = config.parent / "run-agent-background.py"
    startup_file = None
    if not options.no_autostart:
        wrapper, startup_file = background_files(runtime, install_root, config, startup)
    elif not wrapper.is_file():
        wrapper, _ = background_files(runtime, install_root, config, config.parent / ".no-autostart")
        (config.parent / ".no-autostart" / f"AutomationWorkbench-{config.parent.name}.vbs").unlink(missing_ok=True)
        (config.parent / ".no-autostart").rmdir()
    if not options.no_start:
        start_agent(runtime, wrapper, install_root)
    print("执行电脑接入完成。")
    print(f"节点配置：{config}")
    if startup_file:
        print(f"开机启动：{startup_file}")
    print("请回到工作台刷新在线状态，并删除原始连接文件。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path)
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--connection-dir", type=Path)
    parser.add_argument("--startup-dir", type=Path)
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--no-autostart", action="store_true")
    parser.add_argument("--accept-config", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    try:
        run(options)
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, SetupError) else f"接入失败：{type(error).__name__}"
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
