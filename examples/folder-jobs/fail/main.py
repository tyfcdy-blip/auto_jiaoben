"""可控主动失败：验证非零退出、业务失败报告和日志能否被平台观测。"""

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-file", required=True)
    context = json.loads(Path(parser.parse_args().context_file).read_text(encoding="utf-8"))
    output = Path(context["output_dir"])
    if not output.is_absolute() or not output.is_dir():
        raise ValueError("平台输出目录无效")
    if output.resolve().is_relative_to(Path(context["parameters"]["input_dir"]).resolve()):
        raise ValueError("输出目录不能位于只读输入目录内")
    print("synthetic_failure_started", flush=True)
    result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
              "status": "failed", "summary": "合成测试主动失败，未调用任何外部系统",
              "metrics": {"batch_key": context["parameters"]["batch_key"]}, "artifacts": []}
    temporary = output / "result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output / "result.json")
    print("synthetic_failure_expected exit_code=7", file=sys.stderr, flush=True)
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
