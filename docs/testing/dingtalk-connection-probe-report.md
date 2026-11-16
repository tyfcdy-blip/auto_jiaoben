# 钉钉连接测试工作包验证记录（2026-09-19）

## 任务闭环

模式：全过程开发。等级：L3，涉及外部钉钉消息发送、项目权限和审计。基线：`main` 尚无首次提交，仓库原有文件均未跟踪，Git 无法表达本工作包差异；未提交、推送或修改真实机器人凭据。沿用此前对本机 `127.0.0.1:18080` Docker 测试实例升级的授权，仅更新该实例。

| 需求 / 工作包 | 实现 | 用例与状态 |
| --- | --- | --- |
| CP-01 即时测试 | `notifications.py` 复用原钉钉加签与文本发送，单次外发固定测试文字和编号；`routes_operations.py` 新增项目管理员操作接口，连接停用时也可测试 | 隔离 HTTP 模拟加签、固定内容、接口成功与审计；通过 |
| CP-02 错误与保密 | 钉钉错误码、HTTP 错误、无效响应和超时即时反馈；不回显 Webhook、密钥、钉钉原始报错；不加入通知队列、不自动重试 | 失败矩阵、凭据遮蔽、原连接/投递状态；通过 |
| CP-03 权限与界面 | `NotificationSettingsView.vue` 增加“发送测试消息”，提交中禁用重复点击、项目切换清理编号；仅项目管理员可操作 | 未登录/只读/其他项目/不存在；Vue 组件、隔离 Chrome 端到端；通过 |
| CP-04 说明和部署 | `docs/dingtalk-notification-guide.md` 给出核对流程；本机 Docker 测试实例更新 | 镜像构建、OpenAPI、页面、API 健康、未授权 401；通过 |

## 测试证据

| 环境与完整命令 | 退出码 | 结果 |
| --- | ---: | --- |
| `.venv\Scripts\python.exe -m pytest tests/backend/test_channel_probe.py tests/backend/test_notification_policies.py -q` | 0 | 最终 16 通过；隔离 SQLite + `httpx.MockTransport`，无真实外发；原发送器回归通过 |
| `.venv\Scripts\python.exe -m pytest tests/backend -q` | 0 | 最终 110 通过、7 跳过（PostgreSQL 专项）；原发送器回归通过 |
| `.venv\Scripts\python.exe -m ruff check backend/workbench tests/backend/test_channel_probe.py tests/notification_e2e_local.py` | 0 | 静态检查通过 |
| `npm run test -- --reporter=dot`、`npm run build`、`npm run format:check` | 0 | 最终 Vue 151 通过；TypeScript、Vite 构建及格式检查通过，包含切项目时丢弃旧结果 |
| `npx prettier --check e2e/notifications.mjs` | 0 | 浏览器脚本格式通过 |
| `$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE='C:\Program Files\Google\Chrome\Application\chrome.exe'; .venv\Scripts\python.exe tests/notification_e2e_local.py` | 0 | 隔离 SQLite + Chrome：页面点测试、看到结果编号、API 审计存在；发送经 MockTransport 模拟，连接保持停用 |
| `docker build --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm -t automation-workbench:channel-probe-final -f Dockerfile .` | 0 | 最终镜像 `sha256:3f92c11b…` 构建成功；使用与既有实例同系列基础镜像 |
| `docker run --rm --tmpfs /tmp -e WB_DATA_DIR=/tmp/workbench -e WB_DATABASE_URL=sqlite:////tmp/probe-smoke.db --entrypoint python automation-workbench:channel-probe-final -c "from workbench.main import create_app; app=create_app(); print('/api/v1/channels/{channel_id}/test-message' in app.openapi()['paths'])"` | 0 | 最终镜像内新路由存在；首轮不带运行目录/以 `app.routes` 查路由的冒烟命令因测试命令前提错误退出 1，修正后通过，镜像无缺陷 |
| `docker compose --env-file deploy/.env.example -f deploy/compose.yaml --project-name automation-workbench-dev stop api worker`；`.venv\Scripts\python.exe deploy/operations.py backup --project automation-workbench-dev --env-file deploy/.env.example --directory backups/channel-probe-final-before-20260919-155306 --note '本机测试实例更新连接测试页面项目切换反馈；停机前活动运行0、待发消息0、五分钟计划0；执行电脑在途状态未覆盖'` | 0 | 最终更新前活动运行 0、待投递 0、五分钟内计划 0；备份两个文件 SHA-256 与清单一致，tar 安全检查通过；执行电脑在途状态不在备份范围 |
| `docker compose --env-file deploy/.env.example -f deploy/compose.yaml --project-name automation-workbench-dev up -d --no-build --force-recreate init-db api worker` | 0 | 最终 `init-db` 退出 0；API/数据库健康，worker 运行；本次更新前镜像保留为 `automation-workbench:rollback-before-probe-final-20260919`，更早旧版保留为 `automation-workbench:rollback-before-channel-probe-20260919` |
| 本机 `GET /api/v1/health/ready`、`GET /api/openapi.json`、`GET /`、前端 JS、未登录 `POST /api/v1/channels/missing/test-message` | 0 | ready、路由存在、页面 200、按钮文字在资源中、未登录 401；数据库只读检查：30 任务、50 运行、89 告警、1 个启用连接、待投递 0、活动运行 0；后台三循环在推进 |

## 问题与风险

- 真实钉钉 Webhook、加签、关键词及群内呈现**未由 Codex 触发或验证**。项目管理员需在获授权测试群点击一次并核对相同测试编号；接口返回成功只说明钉钉接收，仍要核对群消息。
- 超时、网络失败或响应格式异常时，接口不能确定群是否已经收到。测试消息不自动重试；先核对群再手动重试。反复手动点击会产生多条消息，页面仅阻止提交中的重复点击。
- PostgreSQL 专项用例 7 项因未提供隔离测试库跳过；本机 Docker PostgreSQL 的服务和既有数据健康已验证，但未在真实群外发前执行 PostgreSQL 上的已授权测试接口。独立审查、CI、人工 UI 验收和备份恢复演练未执行。
- 无数据库模式变更。回滚可先停 API/worker，将保留的旧镜像标记回 `automation-workbench:dev` 并重建 API/worker；最终更新前备份在 `backups/channel-probe-final-before-20260919-155306/`，首轮备份在 `backups/channel-probe-before-20260919-154447/`。已发出的群消息不能通过回滚撤回。恢复备份需另行核对执行电脑与派发状态，本工作包未演练。

## 质量状态

| 门禁 | 状态 |
| --- | --- |
| Codex 开发完成 | 是，本机测试实例已更新 |
| 测试计划/用例已生成 | 是，`dingtalk-connection-probe-plan.md` |
| 自动化测试已执行 | 是，见上表 |
| 人工验证已执行 | 否，真实群手动发送待完成 |
| 剩余未验证项 | 真实钉钉送达、独立 PostgreSQL 专项、恢复演练 |
| 独立复核通过 | 未执行 |
| CI 通过 | 未执行 |
| 开发最终确认 | 未获得 |
| 人工代码评审通过 | 未执行 |
| 允许生产发布 | 否；当前仅本机 Docker 测试实例 |

AI 自检结论：**有条件通过**。Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
