"""目录输入的版本声明和受理边界；实际文件访问由绑定的执行器校验。"""
import re
from .security import Problem

DIRECTORY_CAPABILITY = "directory-input-v1"
INPUT_ANNOTATION = "x-workbench-input"


def uses_directory(schema: dict) -> bool:
    return schema.get(INPUT_ANNOTATION) == "directory"


def check_directory_schema(schema: dict) -> None:
    if INPUT_ANNOTATION not in schema:
        return
    if not uses_directory(schema):
        raise Problem(422, "invalid_input_mode", "版本输入方式仅支持directory或省略声明")
    required = schema.get("required", [])
    for field in ("input_dir", "batch_key"):
        definition = schema.get("properties", {}).get(field, {})
        if not isinstance(definition, dict) or definition.get("type") != "string" or field not in required:
            raise Problem(422, "invalid_directory_schema", "目录程序必须声明必填字符串input_dir和batch_key")


def validate_directory_parameters(parameters: dict, *, partial: bool = False) -> None:
    directory = parameters.get("input_dir")
    if directory is not None or not partial:
        if not isinstance(directory, str) or not 1 <= len(directory) <= 2000 or directory != directory.strip():
            raise Problem(422, "invalid_input_directory", "请输入执行电脑可访问的绝对文件夹路径")
        # 不在Linux控制台解析Windows文件系统，也不接收设备命名空间或相对路径。
        normalized = directory.replace("\\", "/")
        drive = bool(re.match(r"^[A-Za-z]:/", normalized))
        unc = normalized.startswith("//") and len([part for part in normalized.split("/") if part]) >= 2
        posix = normalized.startswith("/") and not normalized.startswith("//") and "\\" not in directory
        tail = normalized[2:] if drive else normalized
        if (not (drive or unc or posix) or normalized.startswith(("//?/", "//./"))
                or re.search(r'[\x00-\x1f\x7f<>"|?*]', directory) or ":" in tail
                or any(part in {".", ".."} for part in normalized.split("/"))):
            raise Problem(422, "invalid_input_directory", "文件夹路径必须绝对且不包含越界段或设备路径")
    key = parameters.get("batch_key")
    if key is not None or not partial:
        if not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 180 or re.search(r"[\x00-\x1f\x7f]", key):
            raise Problem(422, "invalid_batch_key", "目录输入必须提供不超过180字符的有效业务批次编号")


def canonical_root(value: str) -> tuple[str, str]:
    validate_directory_parameters({"input_dir": value, "batch_key": "node-config"})
    normalized = value.replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:", normalized) or normalized.startswith("//"):
        return "windows", normalized.casefold()
    return "posix", normalized or "/"


def validate_root_list(values: list[str], *, field: str = "input_roots") -> list[str]:
    if not isinstance(values, list) or len(values) > 30:
        raise Problem(422, "invalid_input_roots", f"{field}必须是最多30项的绝对目录列表")
    result: list[str] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if not isinstance(value, str) or value != value.strip():
            raise Problem(422, "invalid_input_roots", f"{field}包含无效目录")
        key = canonical_root(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def roots_are_subset(project_roots: list[str], host_roots: list[str]) -> bool:
    host = [canonical_root(value) for value in host_roots]
    for value in project_roots:
        flavor, child = canonical_root(value)
        if not any(parent_flavor == flavor and (
                child == parent or parent == "/" or child.startswith(parent + "/"))
                   for parent_flavor, parent in host):
            return False
    return True


def validate_directory_run(schema: dict, task, parameters: dict, batch_id=None, input_label=None) -> None:
    if not uses_directory(schema):
        return
    if not task.node_id or task.requires_input or "batch_key" not in task.business_key_fields:
        raise Problem(422, "invalid_directory_task", "目录任务必须固定执行电脑、按batch_key去重且不能要求平台批次")
    if batch_id or input_label:
        raise Problem(422, "ambiguous_input", "目录任务不能同时使用工作台文件批次")
    validate_directory_parameters(parameters)


def input_mode(schema: dict, requires_input: bool) -> str:
    return "directory" if uses_directory(schema) else "batch" if requires_input else "none"
