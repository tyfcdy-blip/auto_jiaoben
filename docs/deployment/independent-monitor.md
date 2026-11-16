# 独立健康监控

此文档对应 M4.3 已实现的 `backend/workbench/monitor.py`，并非异机上线证明。它用 HTTP 检查工作台，将通知状态存到监控主机的本地文件，直接使用独立钉钉群机器人通道。无需平台数据库连接、平台账号登录或平台通知进程。

## 故障边界与运行位置

必须部署到另一故障域才能覆盖平台主机宕机。另一台与平台共用电源或网络出口的电脑也可能同时失效；同机运行、同一 Docker 主机中的另一容器只能覆盖部分进程故障。独立监控自身的进程退出、主机断电和出口中断应由操作系统服务管理器或既有基础设施监控负责，程序不会尝试通过已经失效的同一通道递归通知。

监控目标应填写工作台实际可访问的服务根地址，包括实际子路径，例如 `https://workbench.example.invalid/automation`，不要追加 `/api/v1`。程序依次检查：

- `/api/v1/health/ready`：返回 HTTP 200 且 `status=ready`。
- `/api/v1/health/components`：携带 Bearer 监控凭据，返回 HTTP 200 且 `status=healthy`。平台端检查 scheduler、reconciler、notifier 最近60秒的成功水位，以及storage_cleanup在配置的清理周期内推进；达到平台文件预警、硬上限或磁盘最低可用空间时返回503，由独立监控机器人发送故障通知。

超时、网络失联、HTTP 非 200（包括 401、503 和跳转）、无法解析或状态不正确均判为故障。检查始终包含两个端点；远程地址必须 HTTPS，启用证书校验，不跟随跳转，也不读取系统代理环境变量。仅 `localhost` 和回环 IP 可用 HTTP，供本机验证。若企业使用私有 CA，应在监控主机正确部署受信任证书，不关闭校验。

## 本地配置与启动

运行环境沿用本项目 Python 3.12 与根目录 `requirements.txt`，无需另加依赖。CLI 复用 `notifications.signed_url` 的签名纯函数，所以需要现有依赖可导入；不会调用 notifier、创建数据库会话或读取平台数据库配置。可把该版本后端源码和锁定依赖单独部署到监控机，无需启动 Web、worker、数据库或执行器。

以下 JSON 的凭据路径都相对于配置文件，状态文件也可以使用绝对路径。凭据文件只允许运行监控的系统账号读取，文件内容仅为对应凭据原文；不要提交到 Git。监控 Token 对应平台 `WB_MONITOR_TOKEN`；它与钉钉 Webhook、签名密钥是不同凭据。

```json
{
  "target_base_url": "https://workbench.example.invalid/automation",
  "monitor_token_file": "secrets/monitor-token.txt",
  "state_file": "state/workbench-monitor.json",
  "webhook_file": "secrets/independent-dingtalk-webhook.txt",
  "signing_secret_file": "secrets/independent-dingtalk-signing-secret.txt",
  "interval_seconds": 30,
  "timeout_seconds": 5,
  "notification_budget": 3
}
```

`webhook_file` 与 `signing_secret_file` 均可省略，此时只向 stdout 输出首次故障和恢复事件 JSON，可由既有日志系统接收。配置 Webhook 时签名密钥仍可省略，但有签名保护的机器人必须提供。只支持 `https://oapi.dingtalk.com/robot/send` 群机器人 Webhook；企业应用机器人并不是此协议。目标群是否支持该机器人、签名与安全设置、出口访问及真实送达尚需在实际群中确认，本轮没有发送真实消息。建议配置与平台通知不同的机器人，并配套独立出口或其他运维渠道；同一钉钉服务故障仍是共同依赖。

配置文件目录、状态目录也应仅允许监控账号写入。状态文件不得与配置或凭据文件同名。不支持把状态放在跨主机共享文件系统上共同调度；一个目标配置应由一个监控实例负责。

在项目 `backend` 目录运行，使用已安装项目依赖的 Python：

```text
python -m workbench.monitor --config /absolute/path/monitor.json --once
python -m workbench.monitor --config /absolute/path/monitor.json
```

Windows 可在任务计划程序中设置 Python 完整路径为程序，参数为 `-m workbench.monitor --config C:\monitor\monitor.json --once`，起始目录为已部署源码的 `backend` 目录。Linux 可由既有服务管理器设置同样的工作目录和命令。常驻模式每轮结束后等待 `interval_seconds`；单轮两个健康请求及最多一个通知请求各有独立超时，因此调度周期须考虑这些耗时。建议每 30 秒一次，禁止重叠启动；程序也用系统文件锁拒绝同一状态文件的重叠执行，锁随进程退出自动释放。

退出码：`--once` 目标健康为 0、目标故障为 1；配置/状态/本地文件权限/锁异常为 2。通知失败只写 stderr 和状态，不改变健康退出码，因此目标恢复但恢复通知失败仍返回 0。应同时采集 stderr，不能只根据退出码判断消息已送达。常驻模式遇到本地配置/存储异常退出 2，交给外部服务管理器处理。

## 事件去重与有限重试

首次健康不发消息；首次故障产生一个事件，持续故障只更新最近检查结果，不因故障原因变化生成新事件。恢复产生新事件，再次故障另起新事件。每个事件保存固定 ID，跨进程重启保留去重记录。状态只有当前事件与最近结果，大小不会随检查次数无限增长；历史通知由外部日志留存。

发送前先持久化扣除一次预算，之后才发请求。失败每隔至少 `interval_seconds` 重试，最多 `notification_budget` 次（允许 1–10）。HTTP 非 200、钉钉业务错误或响应无法解析都不视为送达。超时或发送后进程崩溃可能出现“对方已收到但本地未知”，重试会携带同一个事件 ID，不能承诺消息恰好送达一次。发送预算耗尽写 `monitor_notification_exhausted`；失败写 `monitor_notification_failed`，不记录 Webhook、签名密钥、监控 Token、服务响应正文或底层异常 URL。

恢复发生时，尚未送达的旧故障消息被当前恢复事件替代，避免继续发过时故障。恢复消息可能是群里看到的第一条，因为故障消息可能从未送达。它有独立的有限预算。已经耗尽预算的当前故障不会自动反复通知，需要通过独立运维渠道处理；不能靠本组件保证失效通知渠道自愈。

状态文件用临时文件写入、刷新并原子替换；损坏或目标地址变更时拒绝自动重置。请保留现场并由运维检查，针对新目标配置使用新的状态文件。不要定期清空状态，否则会重置去重与预算，产生重复通知。通知通道轮换保留原事件的发送状态，不自动重发已送达故障。这里不承诺磁盘硬件损坏后的状态恢复。

## 验证记录与边界

测试范围仅为本地隔离环境，所有钉钉调用均经 `httpx.MockTransport`，没有真实外部请求。健康和故障状态机、文件持久化、预算与文件锁是真实实现；另以真实本机 HTTP 服务和独立 CLI 进程检查健康，再关闭本机服务验证失联。测试进程的数据库 URL 故意指向不可连接地址，用于证明没有连接平台数据库。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/monitor -q
.\.venv\Scripts\python.exe -m ruff check backend/workbench/monitor.py tests/monitor
```

覆盖：首次健康静默、首次故障、重启去重、恢复与再故障、503/鉴权失败/跳转/非法响应、超时和失联、发送前落盘、崩溃后预算保留、通知业务失败/限流/非法响应、预算耗尽不递归、恢复替换未送达故障、密钥不泄露、非法配置/状态损坏/目标变更拒绝、重叠检查互斥、真实本机 HTTP/CLI。

2026-09-16 在本项目 Windows / Python 3.12 环境实际执行：pytest 29 通过、0 失败、0 跳过，退出码 0；Ruff 通过，退出码 0。独立代码复核由主线整合安排，本文件不代称通过。

尚未验证：另一主机上的服务托管、真实反向代理/证书/出口网络、真实平台停机与网络故障演练、真实群机器人类型及发送、监控主机故障覆盖。不得据此标记异机部署或生产发布通过。Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
