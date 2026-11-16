# 自动化程序工作台

统一管理自研脚本的带参事件触发、定时调度、真实进程执行、状态与日志、权限和平台钉钉告警。影刀继续负责自身自动化任务，仅通过授权接口交付输入批次并触发本平台任务。

当前实现及版本证据见 `docs/releases/`；架构和接口文档中的计划不能代替已验证功能。正式部署、真实上传和群通知须通过对应授权与验收。

当前已形成 v0.1–v0.5 的本地开发切片：事件/定时、真实执行监控和nine离线链路可联调；**完整系统仍在开发，不能发布生产**。具体未完成项与每包状态见下方当前工作包状态。

- [当前24个工作包状态](docs/current-status.md)
- [测试计划与最终回归](docs/testing/plan.md)
- [最终回归记录](docs/testing/final-regression.md)
- [影刀/外部系统接入步骤](docs/event-integration.md)
- [工作台极简使用说明](docs/quick-start.md)
- [文件夹输入与本机测试](docs/folder-input-guide.md)
- [共享执行电脑与 Windows 共享目录](docs/quick-start.md#六windows-共享文件夹)
- [当前实施契约](docs/implementation-contract.md)
- [实施计划与工作包](实施计划与工作包.md)
- [系统架构](系统工作台架构方案.md)
- [开发接入规范](脚本与程序开发接入规范.md)
- [Docker 部署与备份恢复](docs/deployment/runbook.md)
- [生产部署单](docs/deployment/production-deployment-order.md)
- [部署验证记录](docs/deployment/verification.md)

开发环境使用 Python 3.12、Vue 3、PostgreSQL 17。生产不使用 SQLite；SQLite 仅用于隔离单元测试。Python 依赖见 `requirements.txt` 和 `requirements-dev.txt`，前端依赖使用其锁文件。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

容器启动前请先阅读部署说明，创建本实例的秘密并确认 Docker 引擎可用。不要提交 `.env`、`secrets/`、账单、运行数据或备份。构建不会自动推送镜像；初始化 Git 不等于已提交或发布。
