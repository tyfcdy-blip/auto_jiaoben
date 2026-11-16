"""把目录清单中的多个单页签 XLSX 合并为一个多页签工作簿。"""

import argparse
from copy import copy, deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any
import warnings
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.formula import Tokenizer
from openpyxl.utils.exceptions import InvalidFileException


MAX_FILES = 50
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_CELL_AREA = 2_000_000
UNSUPPORTED_EXTENSIONS = {".xls", ".xlsm", ".xltx", ".xltm", ".xlsb"}
INVALID_SHEET_CHARACTERS = re.compile(r"[\\/*?:\[\]]")
OUTPUT_NAME = "合并结果.xlsx"


class MergeError(Exception):
    """可以安全写入工作台结果摘要的业务错误。"""


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_context(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise MergeError("运行上下文不存在或超过容量限制")
    context = json.loads(path.read_text(encoding="utf-8"))
    required = {"protocol_version", "run_id", "attempt_id", "operation", "parameters", "input_manifest",
                "output_dir", "state_dir", "cancel_file"}
    if not isinstance(context, dict) or set(context) != required or context["protocol_version"] != "1":
        raise MergeError("运行上下文版本或字段无效")
    if context["operation"] != "run":
        raise MergeError("本程序只支持run操作")
    for field in ("run_id", "attempt_id"):
        if not isinstance(context[field], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", context[field]):
            raise MergeError("运行标识无效")
    for field in ("output_dir", "state_dir", "cancel_file"):
        if not isinstance(context[field], str) or not Path(context[field]).is_absolute():
            raise MergeError("运行目录必须是执行器提供的绝对路径")
    return context


def checked_parameters(parameters: Any) -> None:
    if not isinstance(parameters, dict) or set(parameters) - {"input_dir", "batch_key"}:
        raise MergeError("只允许input_dir和batch_key参数")
    if not isinstance(parameters.get("batch_key"), str) or not parameters["batch_key"].strip():
        raise MergeError("batch_key不能为空")


def resolve_sources(context: dict[str, Any]) -> tuple[Path, list[tuple[str, Path]], str]:
    parameters = context["parameters"]
    checked_parameters(parameters)
    input_value = parameters.get("input_dir")
    if not isinstance(input_value, str) or not Path(input_value).is_absolute():
        raise MergeError("input_dir必须是绝对目录")
    root = Path(input_value)
    if root.is_symlink() or not root.is_dir():
        raise MergeError("输入目录不存在或是链接")
    root = root.resolve()
    manifest = context["input_manifest"]
    if not isinstance(manifest, list) or not manifest:
        raise MergeError("输入清单为空")
    names: set[str] = set()
    sources: list[tuple[str, Path]] = []
    unsupported: list[str] = []
    total_bytes = 0
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {"name", "path", "sha256"}:
            raise MergeError("输入清单字段无效")
        name = entry["name"]
        if (not isinstance(name, str) or not name or len(name) > 240 or "\\" in name
                or PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise MergeError("输入清单文件名无效")
        folded = name.casefold()
        if folded in names:
            raise MergeError("输入清单存在重复名称")
        names.add(folded)
        path = Path(entry["path"])
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root)):
            raise MergeError("输入清单文件超出指定目录或不是普通文件")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise MergeError("输入清单摘要无效")
        if file_hash(path) != entry["sha256"]:
            raise MergeError("输入文件内容与工作台清单不一致")
        suffix = path.suffix.lower()
        if suffix in UNSUPPORTED_EXTENSIONS:
            unsupported.append(name)
        elif suffix == ".xlsx" and not path.name.startswith("~$"):
            total_bytes += path.stat().st_size
            sources.append((name, path))
    if unsupported:
        raise MergeError("检测到不支持的Excel格式，仅接受.xlsx：" + "、".join(unsupported[:3]))
    if not sources:
        raise MergeError("输入目录中没有可合并的.xlsx文件")
    if len(sources) > MAX_FILES or total_bytes > MAX_INPUT_BYTES:
        raise MergeError(f"最多处理{MAX_FILES}个.xlsx且输入合计不超过50MiB")
    return root, sorted(sources, key=lambda row: row[0].casefold()), OUTPUT_NAME


def unique_sheet_name(relative_name: str, used: set[str]) -> str:
    stem = PurePosixPath(relative_name).stem.strip().strip("'")
    base = INVALID_SHEET_CHARACTERS.sub("_", stem) or "Sheet"
    base = base[:31]
    candidate = base
    number = 2
    while candidate.casefold() in used:
        suffix = f" ({number})"
        candidate = base[:31 - len(suffix)] + suffix
        number += 1
    used.add(candidate.casefold())
    return candidate


def quoted_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'!"


def rewrite_reference(value: str | None, old_name: str, new_name: str) -> str | None:
    if value is None:
        return None
    replacement = quoted_sheet(new_name)
    quoted_old = quoted_sheet(old_name)
    value = re.sub(re.escape(quoted_old), lambda _: replacement, value, flags=re.IGNORECASE)
    pattern = rf"(?<![A-Za-z0-9_\].']){re.escape(old_name)}!"
    return re.sub(pattern, lambda _: replacement, value, flags=re.IGNORECASE)


def rewrite_formula(formula: Any, old_name: str, new_name: str) -> Any:
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    if "INDIRECT(" in formula.upper() and old_name.casefold() in formula.casefold():
        raise MergeError("发现包含原页签名称的INDIRECT公式，无法安全改写")
    try:
        tokens = Tokenizer(formula).items
    except Exception as error:
        raise MergeError("发现无法解析的公式") from error
    for token in tokens:
        if token.type == "OPERAND" and token.subtype == "RANGE":
            if "[" in token.value and "]" in token.value and "!" in token.value:
                raise MergeError("发现外部工作簿公式引用，拒绝生成可能失效的结果")
            token.value = rewrite_reference(token.value, old_name, new_name)
    return "=" + "".join(token.value for token in tokens)


def rewrite_expression(value: str | None, old_name: str, new_name: str) -> str | None:
    """改写验证/条件格式表达式，同时保留双引号包裹的纯文本列表。"""
    if not isinstance(value, str) or not value:
        return value
    had_equals = value.startswith("=")
    rewritten = rewrite_formula(value if had_equals else "=" + value, old_name, new_name)
    return rewritten if had_equals else rewritten[1:]


def ensure_supported(source: Workbook, worksheet, relative_name: str, caught: list[warnings.WarningMessage]) -> None:
    if caught:
        raise MergeError(f"{relative_name}含无法无损读取的Excel功能：{caught[0].message}")
    if len(source.sheetnames) != 1 or len(source.worksheets) != 1:
        raise MergeError(f"{relative_name}必须且只能包含一个普通工作表")
    if worksheet.sheet_state != "visible":
        raise MergeError(f"{relative_name}的唯一工作表必须可见")
    if worksheet.max_row * worksheet.max_column > MAX_CELL_AREA:
        raise MergeError(f"{relative_name}的单元格区域过大或过于稀疏，最多允许{MAX_CELL_AREA}个位置")
    if getattr(source, "_external_links", None):
        raise MergeError(f"{relative_name}含外部工作簿链接，首版不支持")
    if list(source.defined_names.values()):
        raise MergeError(f"{relative_name}含名称定义，首版不支持")
    if list(worksheet.defined_names.values()):
        raise MergeError(f"{relative_name}含工作表局部名称定义，首版不支持")
    if worksheet.tables:
        raise MergeError(f"{relative_name}含Excel表格对象，首版不支持")
    if getattr(worksheet, "_charts", None) or getattr(worksheet, "_images", None):
        raise MergeError(f"{relative_name}含图表或图片，首版不支持")
    if getattr(worksheet, "_pivots", None):
        raise MergeError(f"{relative_name}含透视对象，首版不支持")


def copy_dimensions(source, destination) -> None:
    for key, dimension in source.row_dimensions.items():
        target = destination.row_dimensions[key]
        for attribute in ("height", "hidden", "outlineLevel", "collapsed", "thickTop", "thickBot"):
            setattr(target, attribute, getattr(dimension, attribute))
    for key, dimension in source.column_dimensions.items():
        target = destination.column_dimensions[key]
        for attribute in ("width", "hidden", "bestFit", "outlineLevel", "collapsed", "min", "max"):
            setattr(target, attribute, getattr(dimension, attribute))


def copy_cells(source, destination, old_name: str, new_name: str) -> int:
    populated = 0
    for row in source.iter_rows():
        for cell in row:
            target = destination[cell.coordinate]
            target.value = rewrite_formula(cell.value, old_name, new_name)
            if cell.value is not None:
                populated += 1
            if cell.has_style:
                target.font = copy(cell.font)
                target.fill = copy(cell.fill)
                target.border = copy(cell.border)
                target.alignment = copy(cell.alignment)
                target.number_format = cell.number_format
                target.protection = copy(cell.protection)
            if cell.hyperlink:
                target.hyperlink = copy(cell.hyperlink)
                if target.hyperlink.location:
                    target.hyperlink.location = rewrite_reference(target.hyperlink.location, old_name, new_name)
            if cell.comment:
                target.comment = copy(cell.comment)
    return populated


def copy_sheet_features(source, destination, old_name: str, new_name: str) -> None:
    copy_dimensions(source, destination)
    for merged in source.merged_cells.ranges:
        destination.merge_cells(str(merged))
    destination.sheet_format = copy(source.sheet_format)
    destination.sheet_properties = deepcopy(source.sheet_properties)
    destination.views = deepcopy(source.views)
    destination.sheet_state = "visible"
    destination.auto_filter = deepcopy(source.auto_filter)
    destination.page_margins = copy(source.page_margins)
    destination.page_setup = copy(source.page_setup)
    destination.print_options = copy(source.print_options)
    destination.sheet_properties.pageSetUpPr = copy(source.sheet_properties.pageSetUpPr)
    destination.print_area = source.print_area
    destination.print_title_cols = source.print_title_cols
    destination.print_title_rows = source.print_title_rows
    destination.sheet_view.showGridLines = source.sheet_view.showGridLines
    destination.sheet_view.zoomScale = source.sheet_view.zoomScale
    destination.freeze_panes = source.freeze_panes
    destination.protection = copy(source.protection)
    for validation in source.data_validations.dataValidation:
        target = deepcopy(validation)
        target.formula1 = rewrite_expression(target.formula1, old_name, new_name)
        target.formula2 = rewrite_expression(target.formula2, old_name, new_name)
        destination.add_data_validation(target)
    for conditional, rules in source.conditional_formatting._cf_rules.items():
        for rule in rules:
            target = deepcopy(rule)
            if target.formula:
                target.formula = [rewrite_expression(formula, old_name, new_name) for formula in target.formula]
            destination.conditional_formatting.add(str(conditional.sqref), target)


def merge_workbooks(context: dict[str, Any]) -> tuple[dict[str, Any], int]:
    root, sources, output_name = resolve_sources(context)
    output_directory = Path(context["output_dir"])
    if (output_directory.is_symlink() or not output_directory.is_dir() or any(output_directory.iterdir())
            or output_directory.resolve().is_relative_to(root)):
        raise MergeError("平台输出目录无效、非空或位于输入目录内")
    target_path = output_directory / output_name
    # openpyxl按扩展名校验格式；临时文件仍保留.xlsx后缀，成功回读后再原子改名。
    temporary = output_directory / ("." + output_name + ".tmp.xlsx")
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    used: set[str] = set()
    total_cells = 0
    try:
        for index, (relative_name, path) in enumerate(sources, start=1):
            if Path(context["cancel_file"]).exists():
                raise MergeError("任务已收到停止请求")
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    source = load_workbook(path, read_only=False, data_only=False, keep_links=True, rich_text=True)
            except (InvalidFileException, BadZipFile, OSError, ValueError, KeyError) as error:
                raise MergeError(f"无法读取{relative_name}，文件可能损坏或受密码保护") from error
            try:
                worksheet = source.worksheets[0] if source.worksheets else None
                if worksheet is None:
                    raise MergeError(f"{relative_name}不包含普通工作表")
                ensure_supported(source, worksheet, relative_name, caught)
                new_name = unique_sheet_name(relative_name, used)
                destination = workbook.create_sheet(new_name)
                total_cells += copy_cells(worksheet, destination, worksheet.title, new_name)
                copy_sheet_features(worksheet, destination, worksheet.title, new_name)
                print(f"processed_xlsx={relative_name} sheet={new_name} progress={index}/{len(sources)}", flush=True)
            finally:
                source.close()
        workbook.save(temporary)
        if temporary.stat().st_size > MAX_OUTPUT_BYTES:
            raise MergeError("合并结果超过20MiB，无法作为当前工作台产物上传")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            check = load_workbook(temporary, read_only=False, data_only=False, keep_links=True, rich_text=True)
        try:
            if caught or check.sheetnames != [sheet.title for sheet in workbook.worksheets]:
                raise MergeError("合并结果回读校验未通过")
        finally:
            check.close()
        temporary.replace(target_path)
    finally:
        temporary.unlink(missing_ok=True)
        workbook.close()
    result = {
        "protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
        "status": "success", "summary": f"已把{len(sources)}个单页签Excel合并为{len(sources)}个页签",
        "metrics": {"files": len(sources), "sheets": len(sources), "populated_cells": total_cells,
                    "batch_key": context["parameters"]["batch_key"]},
        "artifacts": [{"name": output_name, "required": True}],
    }
    return result, 0


def write_result(output: Path, result: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or (output / "result.json").exists():
        raise MergeError("结果目录不安全或已有结果")
    temporary = output / ".result.json.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(output / "result.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-file", type=Path, required=True)
    options = parser.parse_args()
    context = None
    try:
        context = load_context(options.context_file)
        result, exit_code = merge_workbooks(context)
    except (MergeError, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, MergeError) else f"Excel合并失败：{type(error).__name__}"
        print(message, file=sys.stderr, flush=True)
        if context is None:
            return 2
        result = {"protocol_version": "1", "run_id": context["run_id"], "attempt_id": context["attempt_id"],
                  "status": "canceled" if message == "任务已收到停止请求" else "failed",
                  "summary": message, "artifacts": []}
        exit_code = 130 if result["status"] == "canceled" else 2
    try:
        write_result(Path(context["output_dir"]), result)
    except (MergeError, OSError, ValueError, TypeError):
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
