"""把运行协议映射到原nine命令行；仅报表和离线结构预检，不复制业务计算。"""

import argparse
import ast
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


PACKAGE = Path(__file__).resolve().parent
ALLOWED_OPERATIONS = {"check": "pdd_billing_check.py", "preflight": "pdd_billing_warehouse_load.py"}
MAXIMUM_SECONDS = 240


class AdapterError(Exception):
    """对外只输出受控错误说明，不带输入业务原文。"""


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 180 or value in {".", ".."}
            or re.search(r'[\\/:\x00-\x1f<>"|?*]', value) or value.endswith((".", " "))
            or value.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise AdapterError("输入文件名不符合安全约定")
    return value


def load_context(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 128 * 1024:
        raise AdapterError("运行上下文超过限制")
    context = json.loads(path.read_text(encoding="utf-8"))
    expected = {"protocol_version", "run_id", "attempt_id", "operation", "parameters", "input_manifest",
                "output_dir", "state_dir", "cancel_file"}
    if not isinstance(context, dict) or set(context) != expected or context["protocol_version"] != "1":
        raise AdapterError("运行上下文版本或字段无效")
    for field in ("run_id", "attempt_id"):
        if not isinstance(context[field], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", context[field]):
            raise AdapterError("运行标识无效")
    for field in ("output_dir", "state_dir", "cancel_file"):
        if not isinstance(context[field], str) or not Path(context[field]).is_absolute():
            raise AdapterError("运行目录必须由执行器提供绝对路径")
    return context


def resolve_inputs(context: dict[str, Any]) -> tuple[str, Path, list[Path]]:
    parameters = context["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"period", "mapping_file", "bill_files"}:
        raise AdapterError("仅允许账期、店铺映射文件名和账单文件名列表")
    period = parameters["period"]
    if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
        raise AdapterError("账期格式必须为YYYY-MM")
    try:
        date(int(period[:4]), int(period[5:]), 1)
    except ValueError as error:
        raise AdapterError("账期无效") from error
    names = parameters["bill_files"]
    if not isinstance(names, list) or not 1 <= len(names) <= 20:
        raise AdapterError("账单文件列表数量无效")
    names = [safe_name(name) for name in names]
    mapping_name = safe_name(parameters["mapping_file"])
    if mapping_name.casefold() in {name.casefold() for name in names} or len({name.casefold() for name in names}) != len(names):
        raise AdapterError("账单重复或映射文件混入账单")
    manifest = context["input_manifest"]
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 21:
        raise AdapterError("输入清单数量无效")
    files: dict[str, Path] = {}
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {"name", "path", "sha256"}:
            raise AdapterError("输入清单字段无效")
        name = safe_name(entry["name"])
        path = Path(entry["path"])
        if (name.casefold() in {existing.casefold() for existing in files}
                or not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.name != name or path.suffix.lower() != ".xlsx"):
            raise AdapterError("输入清单路径、扩展名或唯一性无效")
        if not isinstance(entry["sha256"], str) or file_hash(path) != entry["sha256"]:
            raise AdapterError("输入内容与已冻结清单不一致")
        files[name] = path
    if set(files) != {mapping_name, *names}:
        raise AdapterError("声明文件必须与输入批次精确对应")
    return period, files[mapping_name], [files[name] for name in names]


def source_path(operation: str) -> Path:
    name = ALLOWED_OPERATIONS.get(operation)
    if name is None:
        raise AdapterError("本版本只允许check和preflight，正式upload尚未授权")
    script = PACKAGE / "legacy" / name
    record = json.loads((PACKAGE / "source-manifest.json").read_text(encoding="utf-8"))
    if script.is_symlink() or file_hash(script) != record["sha256"].get(name):
        raise AdapterError("原程序内容与打包校验值不符")
    return script


def offline_configuration(script: Path, mapping: Path, output: Path) -> Path:
    # 复用旧程序的目标表元数据，避免再维护一份可能漂移的业务映射。
    target_specs = None
    for statement in ast.parse(script.read_text(encoding="utf-8-sig")).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TARGET_SPECS" for target in statement.targets
        ):
            target_specs = ast.literal_eval(statement.value)
    if not isinstance(target_specs, dict):
        raise AdapterError("原程序目标表元数据无法识别")
    work = output / ".nine-work"
    work.mkdir()
    configuration = {
        "platform_api": {"base_url": "http://127.0.0.1:1", "app_key": "OFFLINE_NOT_A_CREDENTIAL",
                         "app_secret": "OFFLINE_NOT_A_CREDENTIAL", "sign_method": "md5_sorted_params_append_secret",
                         "timestamp_unit": "milliseconds"},
        "targets": {key: {"api_id": "OFFLINE_NOT_A_TARGET", "table_name": value["table_name"],
                          "sheet_name": value["sheet_name"]} for key, value in target_specs.items()},
        "upload": {"batch_size": 100, "created_at_timezone": "Asia/Shanghai", "run_id_prefix": "offline"},
        "paths": {"shop_account_file": str(mapping), "manifest_dir": str(output),
                  "request_log_dir": str(work / "unused-requests"),
                  "id_state_file": str(work / "unused-id-state.json")},
    }
    path = work / "offline-config.json"
    path.write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
    return path


def execute_legacy(command: list[str], cancel: Path) -> tuple[int, str | None]:
    if cancel.exists():
        return 130, "canceled"
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "HOME", "LANG"}}
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    deadline = time.monotonic() + MAXIMUM_SECONDS
    with subprocess.Popen(command, cwd=PACKAGE, env=environment) as process:
        while process.poll() is None:
            observation = "canceled" if cancel.exists() else "timeout" if time.monotonic() >= deadline else None
            if observation:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                return 130 if observation == "canceled" else 124, observation
            time.sleep(0.05)
        return process.returncode, None


def run(context: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    operation = context["operation"]
    script = source_path(operation)
    period, mapping, bills = resolve_inputs(context)
    output = Path(context["output_dir"])
    if output.is_symlink() or (output.exists() and any(output.iterdir())):
        raise AdapterError("输出目录非空或为链接，拒绝覆盖既有尝试产物")
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-X", "utf8", "-B", "-I", str(PACKAGE / "legacy_launcher.py"), str(script),
               *map(str, bills), "--period", period, "--shop-account-file", str(mapping)]
    if operation == "check":
        command += ["--output-dir", str(output)]
    else:
        command += ["--config", str(offline_configuration(script, mapping, output)), "--run-id", context["run_id"]]
    print(f"nine operation={operation} run_id={context['run_id']} input_files={len(bills)}", flush=True)
    exit_code, observation = execute_legacy(command, Path(context["cancel_file"]))
    artifacts = sorted(path.name for path in output.iterdir() if path.is_file())
    metrics: dict[str, Any] = {"legacy_exit_code": exit_code, "input_files": len(bills), "network_allowed": False}
    if exit_code == 0 and operation == "preflight":
        manifest_path = output / f"{context['run_id']}_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("execute") is not False or manifest.get("run_id") != context["run_id"]:
            raise AdapterError("预检结果不属于当前离线操作")
        metrics["total_rows"] = manifest["total_rows"]
        summary = "原程序结构预检通过；未上传，未验证接口连通性及金额、日期逐字段有效性"
    elif exit_code == 0:
        if len(list(output.glob("*.xlsx"))) != len(bills):
            raise AdapterError("原程序正常退出但报表数量不匹配")
        summary = "原程序报表生成完成；外部或截图金额仍按原业务规则另行核对"
    else:
        summary = f"原程序未成功完成，退出码{exit_code}；现有产物仅供核对，不代表成功"
    if observation:
        summary = "离线操作已取消" if observation == "canceled" else "离线操作超时并已停止"
    result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
              "status": "success" if exit_code == 0 else "canceled" if observation == "canceled" else "failed",
              "summary": summary, "metrics": metrics,
              "artifacts": [{"name": name, "required": exit_code == 0} for name in artifacts]}
    return exit_code, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-file", type=Path, required=True)
    args = parser.parse_args()
    context = None
    try:
        context = load_context(args.context_file)
        code, result = run(context)
    except (AdapterError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, AdapterError) else f"适配失败：{type(error).__name__}"
        print(message, file=sys.stderr)
        if context is None:
            return 1
        code = 1
        result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
                  "status": "failed", "summary": message}
    output = Path(context["output_dir"])
    # 上下文由执行器授权；不覆盖旧结果，避免重放把一次成功记录变成新的执行。
    if output.is_symlink() or (output / "result.json").exists():
        return 1
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / ".result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(output / "result.json")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
