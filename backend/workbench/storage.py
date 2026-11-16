import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import stat
import shutil
import uuid
import zipfile
from jsonschema import Draft202012Validator, ValidationError, SchemaError
from .security import Problem
from .input_sources import check_directory_schema


def safe_name(name: str) -> str:
    if (not name or len(name) > 180 or name in {".", ".."} or re.search(r'[\\/:\x00-\x1f<>"|?*]', name)
            or name.endswith((".", " ")) or name.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}):
        raise Problem(422, "invalid_filename", "文件名不安全")
    return name


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def check_schema(schema):
    try:
        if len(canonical(schema)) > 32000 or schema.get("type") != "object":
            raise ValueError()
        # 禁止远端引用，避免参数校验造成服务端任意网络读取。
        if '"$ref"' in canonical(schema) or '"$dynamicRef"' in canonical(schema):
            raise ValueError()
        Draft202012Validator.check_schema(schema)
        if schema.get("additionalProperties") is not False:
            raise ValueError()
    except (SchemaError, ValueError, TypeError):
        raise Problem(422, "invalid_schema", "参数模式须为封闭 object，禁止外部引用") from None
    check_directory_schema(schema)


RESERVED_PARAMETERS = {"command", "version_id", "node_id", "credential", "connection", "operation", "run_id", "attempt_id"}


def validate_parameters(schema, parameters, partial=False):
    try:
        serialized = canonical(parameters)
    except (ValueError, TypeError):
        raise Problem(422, "invalid_parameters", "参数必须是标准 JSON，不能包含非有限数字") from None
    if len(serialized) > 32000 or RESERVED_PARAMETERS.intersection(parameters):
        raise Problem(422, "invalid_parameters", "参数超过限制或包含受控字段")
    validator_schema = {**schema, "required": []} if partial else schema
    try:
        Draft202012Validator(validator_schema).validate(parameters)
    except ValidationError:
        raise Problem(422, "invalid_parameters", "参数不符合已发布版本的定义") from None


def validate_command(command):
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,39}", command[0]):
        raise Problem(422, "invalid_command", "启动项必须使用节点登记的运行时别名")
    for argument in command:
        if len(argument) > 500 or any(character in argument for character in "\x00\r\n"):
            raise Problem(422, "invalid_command", "启动参数无效")
    entry = PurePosixPath(command[1])
    if entry.is_absolute() or ".." in entry.parts or "\\" in command[1] or ":" in command[1]:
        raise Problem(422, "invalid_command", "入口必须位于程序包内")


def validate_zip(payload: bytes, entry: str):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set()
            total = 0
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                normalized = str(path).casefold()
                raw_name = member.filename.rstrip("/") if member.is_dir() else member.filename
                if (path.is_absolute() or ".." in path.parts or "\\" in member.filename or ":" in member.filename
                        or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or normalized in names or len(raw_name) > 240
                        or any(part in {"", ".", ".."} for part in raw_name.split("/"))):
                    raise ValueError()
                for part in path.parts:
                    safe_name(part)
                names.add(normalized)
                total += member.file_size
                if total > 100 * 1024 * 1024 or len(names) > 1000 or member.flag_bits & 1:
                    raise ValueError()
            if entry.casefold() not in names or archive.testzip() is not None:
                raise ValueError()
    except (zipfile.BadZipFile, ValueError, RuntimeError, NotImplementedError):
        raise Problem(422, "invalid_bundle", "程序包格式、入口、大小或路径校验失败") from None


async def read_upload(request, maximum):
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > maximum:
            raise Problem(413, "upload_too_large", "文件超过允许大小")
    if not payload:
        raise Problem(422, "empty_upload", "不能上传空文件")
    return bytes(payload)


def storage_usage(settings):
    directory = settings.data_dir / "objects"
    used = 0
    if directory.is_dir():
        for item in directory.iterdir():
            try:
                if item.is_file() and not item.is_symlink() and re.fullmatch(r"[a-f0-9]{64}", item.name):
                    used += item.stat().st_size
            except FileNotFoundError:
                # 监控扫描可能与已受数据库锁保护的清理提交交错；消失的对象不计占用。
                continue
    disk = shutil.disk_usage(settings.data_dir)
    return {"used_bytes": used, "free_bytes": disk.free,
            "warning_bytes": settings.storage_warning_bytes,
            "max_bytes": settings.storage_max_bytes,
            "min_free_bytes": settings.storage_min_free_bytes,
            "warning": used >= settings.storage_warning_bytes}


def store_bytes(settings, payload):
    sha = hashlib.sha256(payload).hexdigest()
    directory = settings.data_dir / "objects"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / sha
    if target.exists():
        stored_sha = None
        if target.is_file() and not target.is_symlink() and target.stat().st_size == len(payload):
            with target.open("rb") as stream:
                stored_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if stored_sha != sha:
            raise Problem(507, "storage_corrupt", "平台文件存储异常，请联系管理员")
        return sha
    status = storage_usage(settings)
    if (status["used_bytes"] + len(payload) > settings.storage_max_bytes
            or status["free_bytes"] - len(payload) < settings.storage_min_free_bytes):
        raise Problem(507, "storage_capacity", "平台文件容量不足，请联系管理员清理或扩容")
    if not target.exists():
        temporary = directory / (".upload-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return sha


def object_path(settings, sha):
    if not re.fullmatch(r"[a-f0-9]{64}", sha or ""):
        raise Problem(404, "missing_file", "文件不可用")
    path = settings.data_dir / "objects" / sha
    if not path.is_file() or path.is_symlink():
        raise Problem(404, "missing_file", "文件不可用")
    return path
