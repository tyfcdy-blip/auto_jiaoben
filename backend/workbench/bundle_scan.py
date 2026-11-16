"""程序包静态预检。不执行上传代码，也不联网解析依赖。"""
import ast
import io
import json
from pathlib import PurePosixPath
import re
import zipfile


SCANNER_VERSION = "1"
TEXT_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".ps1", ".bat", ".cmd", ".sh",
                   ".json", ".yaml", ".yml", ".toml"}
OPAQUE_EXECUTABLES = {".exe", ".dll", ".pyd", ".so", ".dylib", ".jar", ".class", ".com", ".msi"}
MAX_TEXT_FILE = 1024 * 1024
MAX_SCANNED_TEXT = 5 * 1024 * 1024
MAX_FINDINGS = 100


class FindingList(list):
    def __init__(self):
        super().__init__()
        self.counts = {"error": 0, "high": 0, "warning": 0}
        self.truncated = False


def _finding(findings, severity, code, filename, message, line=None):
    findings.counts[severity] += 1
    item = {"severity": severity, "code": code, "file": filename, "message": message}
    if line:
        item["line"] = line
    if len(findings) >= MAX_FINDINGS:
        findings.truncated = True
        if severity == "error":
            replaceable = next((index for index in range(len(findings) - 1, -1, -1)
                                if findings[index]["severity"] != "error"), None)
            if replaceable is not None:
                findings[replaceable] = item
        return
    findings.append(item)


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _scan_python(filename, text, findings):
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as error:
        _finding(findings, "error", "python_syntax_error", filename,
                 "Python 语法无法解析", error.lineno)
        return
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name.split('.')[0]] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                aliases[imported.asname or imported.name] = f"{node.module}.{imported.name}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        root, separator, remainder = name.partition('.')
        if root in aliases:
            name = aliases[root] + (separator + remainder if separator else '')
        line = getattr(node, "lineno", None)
        if name in {"eval", "exec", "compile", "builtins.eval", "builtins.exec"}:
            _finding(findings, "high", "dynamic_code_execution", filename,
                     "使用动态代码执行，必须确认输入来源和隔离边界", line)
        if name == "os.system":
            _finding(findings, "high", "shell_command", filename,
                     "通过系统 shell 执行命令，需审查命令拼接与注入风险", line)
        if name.startswith("subprocess."):
            shell_true = any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
                             and keyword.value.value is True for keyword in node.keywords)
            if shell_true:
                _finding(findings, "high", "subprocess_shell", filename,
                         "subprocess 使用 shell=True，需审查命令注入风险", line)
        if name in {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "dill.load", "dill.loads"}:
            _finding(findings, "high", "unsafe_deserialization", filename,
                     "反序列化不可信内容可能执行任意代码", line)
        destructive_method = isinstance(node.func, ast.Attribute) and node.func.attr in {"unlink", "rmdir"}
        if (name in {"shutil.rmtree", "os.remove", "os.unlink", "pathlib.Path.unlink", "pathlib.Path.rmdir"}
                or destructive_method):
            _finding(findings, "high", "destructive_filesystem", filename,
                     "程序包含删除文件或目录操作，需核对允许目录和回滚方式", line)
        if name in {"requests.get", "requests.post", "requests.put", "requests.patch", "requests.delete",
                    "httpx.get", "httpx.post", "httpx.put", "httpx.patch", "httpx.delete"}:
            verify_false = any(keyword.arg == "verify" and isinstance(keyword.value, ast.Constant)
                               and keyword.value.value is False for keyword in node.keywords)
            if verify_false:
                _finding(findings, "high", "tls_verification_disabled", filename,
                         "外部请求关闭了 TLS 证书校验", line)


PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "high", "embedded_private_key",
     "程序包疑似包含私钥原文"),
    (re.compile(r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*=\s*['\"][^'\"]{8,}['\"]"),
     "high", "embedded_secret", "代码疑似硬编码密码、密钥或令牌"),
    (re.compile(r"(?i)\b(?:Invoke-Expression|iex)\b|-(?:EncodedCommand|enc)\b"),
     "high", "powershell_dynamic_execution", "PowerShell 包含动态执行或编码命令"),
    (re.compile(r"\b(?:eval\s*\(|new\s+Function\s*\(|child_process\.(?:exec|execSync)\s*\()"),
     "high", "script_dynamic_execution", "脚本包含动态代码或 shell 执行"),
    (re.compile(r"(?im)^\s*(?:rm\s+-rf\s+/(?:\s|$)|del\s+/[sq].*\s+[a-z]:\\|rmdir\s+/s\s+/q\s+[a-z]:\\)"),
     "error", "destructive_root_command", "检测到针对系统根目录的破坏性删除命令"),
]


def _scan_patterns(filename, text, findings):
    for pattern, severity, code, message in PATTERNS:
        if len(findings) >= MAX_FINDINGS:
            match = pattern.search(text)
            if match:
                _finding(findings, severity, code, filename, message)
            continue
        remaining = MAX_FINDINGS - len(findings)
        for index, match in enumerate(pattern.finditer(text)):
            if index >= remaining:
                findings.counts[severity] += 1
                break
            line = text.count("\n", 0, match.start()) + 1
            _finding(findings, severity, code, filename, message, line)


def scan_bundle(payload: bytes, entry: str, runtime: str | None = None) -> dict:
    findings = FindingList()
    scanned_files = 0
    skipped_files = 0
    scanned_bytes = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            filename = member.filename
            suffix = PurePosixPath(filename).suffix.lower()
            is_entry = filename.casefold() == entry.casefold()
            if is_entry and suffix not in TEXT_EXTENSIONS and suffix not in OPAQUE_EXECUTABLES:
                normalized_runtime = (runtime or "").lower()
                if normalized_runtime.startswith("python"):
                    suffix = ".py"
                elif normalized_runtime in {"node", "nodejs"}:
                    suffix = ".js"
                elif normalized_runtime in {"powershell", "pwsh"}:
                    suffix = ".ps1"
                else:
                    skipped_files += 1
                    _finding(findings, "high", "entry_scan_unsupported", filename,
                             "入口文件类型无法静态解析，必须人工复核运行时与源码")
                    continue
            if suffix in OPAQUE_EXECUTABLES:
                skipped_files += 1
                _finding(findings, "high", "opaque_executable", filename,
                         "二进制可执行文件无法静态确认行为，必须人工复核来源和签名")
                continue
            if suffix not in TEXT_EXTENSIONS:
                continue
            if member.file_size > MAX_TEXT_FILE or scanned_bytes + member.file_size > MAX_SCANNED_TEXT:
                skipped_files += 1
                _finding(findings, "high", "text_scan_limit", filename,
                         "文本文件超过静态扫描上限，内容未完整检查")
                continue
            raw = archive.read(member)
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                skipped_files += 1
                severity = "error" if suffix in {".py", ".json"} else "high"
                _finding(findings, severity, "text_decode_failed", filename,
                         "文本文件不是可识别的 UTF-8，无法完成静态检查")
                continue
            scanned_files += 1
            scanned_bytes += len(raw)
            if suffix == ".py":
                _scan_python(filename, text, findings)
            elif suffix == ".json":
                try:
                    json.loads(text)
                except (ValueError, TypeError):
                    _finding(findings, "error", "json_syntax_error", filename,
                             "JSON 文件格式无效")
            elif is_entry and suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".ps1", ".bat", ".cmd", ".sh"}:
                _finding(findings, "high", "entry_syntax_not_verified", filename,
                         "当前扫描器不能完整验证该入口语言的语法，必须人工复核或在隔离环境验证")
            _scan_patterns(filename, text, findings)
    errors = findings.counts["error"]
    high = findings.counts["high"]
    decision = "blocked" if errors else "review" if high else "pass"
    return {
        "scanner_version": SCANNER_VERSION,
        "decision": decision,
        "coverage": "partial" if skipped_files else "full",
        "entry": entry,
        "scanned_files": scanned_files,
        "skipped_files": skipped_files,
        "summary": {"errors": errors, "high": high, "warnings": findings.counts["warning"]},
        "findings_truncated": findings.truncated,
        "findings": findings,
    }
