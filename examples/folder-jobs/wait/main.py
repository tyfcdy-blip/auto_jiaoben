"""有界等待并响应取消文件；将平台超时设短可验证进程终止和日志留存。"""

import argparse
import json
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    context = json.loads(Path(parser.parse_args().context_file).read_text(encoding="utf-8"))
    output = Path(context["output_dir"])
    if not output.is_absolute() or not output.is_dir():
        raise ValueError("平台输出目录无效")
    if output.resolve().is_relative_to(Path(context["parameters"]["input_dir"]).resolve()):
        raise ValueError("输出目录不能位于只读输入目录内")
    seconds = context["parameters"].get("wait_seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 600:
        raise ValueError("wait_seconds 必须在 1 至 600 秒之间")
    cancel = Path(context["cancel_file"])
    started = time.monotonic()
    last_tick = -1
    canceled = False
    while time.monotonic() - started < seconds:
        if cancel.exists():
            canceled = True
            print("cancel_file_observed", flush=True)
            break
        elapsed = int(time.monotonic() - started)
        if elapsed != last_tick:
            print(f"waiting elapsed_seconds={elapsed}", flush=True)
            last_tick = elapsed
        time.sleep(0.1)
    result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
              "status": "canceled" if canceled else "success",
              "summary": "已响应取消文件" if canceled else "合成等待任务正常完成",
              "metrics": {"elapsed_seconds": round(time.monotonic() - started, 2),
                          "batch_key": context["parameters"]["batch_key"]}, "artifacts": []}
    temporary = output / "result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output / "result.json")
    return 130 if canceled else 0


if __name__ == "__main__":
    raise SystemExit(main())
