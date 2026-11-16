"""在日志与外发摘要边界共用的最小凭据遮蔽规则。"""
import re


def redact(text: str) -> str:
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[REDACTED]", text)
    return re.sub(r"(?i)((?:password|app_secret|secret|access_token|token)\s*[=:]\s*)[^\s,;]+",
                  r"\1[REDACTED]", text)
