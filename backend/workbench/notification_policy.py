"""告警类别和等级只由平台事实决定，脚本不能指定接收人或等级。"""

ALERT_KINDS = (
    "unknown", "partial", "schedule_misfire", "schedule_failed",
    "failed", "start_rejected", "node_offline", "schedule_delayed",
)
DEFAULT_KINDS = tuple(kind for kind in ALERT_KINDS if kind != "schedule_delayed")
SEVERITY = {
    "unknown": "critical",
    "partial": "critical",
    "schedule_misfire": "critical",
    "schedule_failed": "critical",
    "failed": "warning",
    "start_rejected": "warning",
    "node_offline": "warning",
    "schedule_delayed": "warning",
    "recovery": "info",
}
SEVERITY_LABEL = {"critical": "紧急", "warning": "警告", "info": "信息"}


def alert_severity(kind: str) -> str:
    # 旧记录或后续新增类别不得因为缺少前端映射而静默不显示。
    return SEVERITY.get(kind, "warning")
