"""只运行已打包的报表/预检入口；审计钩子阻止误调用网络，不作为恶意代码沙箱。"""

from pathlib import Path
import runpy
import sys


def prohibit_external_calls(event: str, arguments: tuple) -> None:
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("offline_adapter_network_or_child_process_forbidden")


def main() -> None:
    script = Path(sys.argv[1]).resolve()
    if script.name not in {"pdd_billing_check.py", "pdd_billing_warehouse_load.py"}:
        raise SystemExit("不允许的旧程序入口")
    if "--execute" in sys.argv[2:]:
        raise SystemExit("此适配器禁止正式上传")
    sys.addaudithook(prohibit_external_calls)
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
