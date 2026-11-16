# 前端增量验证记录

2026-09-17新增：接入简化与界面改版最终75项前端测试、构建/格式检查、本地与Docker各17项真实浏览器流程通过。当前截图与总结以[新验收记录](../docs/testing/usability-redesign-report.md)及其持久证据目录为准。下文保留9月16日原版本历史记录，不覆盖历史测试口径。

日期：2026-09-16。前端目录独立写入，未修改业务脚本、后端、执行器或部署文件。

## 增量门禁

| 阶段 | 实现范围 | 实际验证 |
| --- | --- | --- |
| v0.1 | 登录、内存会话、项目切换/创建、总览、程序/版本/ZIP 发布、任务登记 | 构建退出 0；2 文件 9 项组件/单元测试通过。详见 verification-v0.1.md |
| v0.2 | 运行详情/日志/取消/核对、节点、输入批次 | 构建退出 0；3 文件 12 项测试通过；新增旧响应竞态、日志 XSS、未知结果、一次性凭据场景 |
| v0.3 / v0.4 增量联验 | 事件调用方、定时带参、告警/连接、用户/成员/审计 | 构建退出 0；5 文件 16 项测试通过。网络重试沿用 event_id；定时保持日期模板、默认关闭；告警发送和处理分开 |
| 后续接口补齐 | BASE_URL 子路径、选项分页、标签等待输入、调用方文件授权、签发凭据时防关闭、告警恢复/关闭 | 最新构建退出 0；6 文件 22 项测试通过；格式检查退出 0。新增字段及告警关闭已在新版后端真实网络复验 |
| 独立复核修复 | 运行详情根据所属项目显示停止/核对入口；调用方交付授权明确整个项目范围 | 新增 6 个跨项目/角色组合，最新 6 文件 28 项测试全过；构建与格式检查退出 0 |

## 真实浏览器联调

已在独立 PostgreSQL schema、本机 HTTP 服务与真实 Chromium 上分别完成根路径和子路径完整链路，每轮 17 项通过，退出码均为 0。根路径于 2026-09-16 19:24（北京时间）在本地 10914 端口执行；子路径于 19:28 在本地 2011 端口 `/automation` 执行；权限修复后再次在本地 6465 端口完成最终根路径全链路回归。浏览器为 Chromium 149.0.7827.55。未 Mock 核心接口或执行器，未调用真实业务外部系统、未创建/发送真实钉钉消息。

覆盖：登录、项目、程序、ZIP 上传发布、任务、合成输入上传冻结、受理后立即查询、排队取消、定时启停及 `${previous_month}` 标签保存、节点与维护、调用方及撤销/文件上传授权、用户与成员角色、审计/告警读取、移动导航、刷新会话、真实开发执行器运行合成程序、成功日志与鉴权下载产物、真实脚本失败后告警确认与关闭。下载内容与合成文件核对一致。

该轮浏览器错误 0、控制台警告 0、API 错误 0。截图检查过 1440×1000 桌面与 390×844 移动视口，表格在自己的容器内滚动。一次性凭据/非空密码区域遮罩；临时节点 token 在结束后删除。

证据：每次执行会更新 `e2e-results/summary.json` 与 `01-login-desktop.png`、`02-overview-desktop.png`、`03-run-detail-desktop.png`、`04-tasks-mobile.png`、`05-success-artifact-desktop.png`、`06-alert-closed-desktop.png`，当前为权限修复后的最终根路径结果。子路径总结另存 `summary-subpath.json`。各轮终端均有 17 项通过及退出码 0 的记录。生成文件已在本目录 .gitignore 排除，未将测试秘密提交仓库。旧的 `failure.png` 属于已修复缺陷的历史截图，不是最近一轮结果。

## 发现及复验

- 生产构建曾发现多语句内联事件经格式化后无法解析；改为命名处理函数，构建复验通过。
- 浏览器首次运行发现成员选择器精确名称不清晰，补充显式可访问名称；复验通过。
- 后端只提供 assets 目录，原 favicon 根路径返回 404；改为 Vite 管理资产，复验通过。
- 真实网络发现任务受理返回后立即读取偶发 404。已将运行编号和证据交主 agent，后端修复提交时机；完整浏览器复验通过。前端没有通过延迟或跳过断言掩盖问题。
- 子路径 API 最初固定根路径；已统一 BASE_URL、示例接口及下载地址，并通过根路径/子路径单元和真实浏览器验证。子路径执行器使用含 `/automation` 的 base_url，上传、日志和鉴权下载全部通过；专用子路径构建位于 `runtime/frontend-subpath`，默认 dist 未被覆盖。这是本地挂载验证，不冒称 Docker 容器实机通过。
- 表单资源选项不再只使用第一页；逐页读取且限制资源总量，程序版本按所选程序加载。
- 主 agent 独立复核发现运行详情误用导航所选项目权限，且漏掉项目管理员核对入口。已改为按 run.project_id 查找成员角色，平台管理员例外；覆盖 viewer/operator/developer/project_admin/无成员记录/平台管理员六种组合，每种均验证运行中停止入口和 unknown 核对入口。后端鉴权仍为最终边界。
- 调用方文件交付授权文案已明确覆盖整个项目输入批次，不受任务选择范围限制，组件用例核对提示及默认关闭状态。

## 可重跑命令与结果

在 frontend 目录：

| 命令 | 已获得结果 |
| --- | --- |
| `npm ci` / 首次 `npm install` | 使用固定直接依赖和 package-lock；首次安装退出 0 |
| `npm test` | 退出 0；28 通过，0 失败，0 跳过 |
| `npm run build` | 退出 0；TypeScript 与生产构建通过 |
| `npm run format:check` | 退出 0 |
| `npm run test:e2e` | 最新完整真实链路 17 通过，0 失败，退出 0；使用环境变量指定本机 Chromium 可执行文件 |
| `npm audit --registry=https://registry.npmjs.org --omit=dev --audit-level=high` | 退出 0，报告 0 漏洞，仅覆盖生产依赖 |

默认 npm 镜像的审计 API 返回 404、明确不支持该端点。随后仅本次审计命令改用官方 registry 成功，未更改用户全局配置；不把镜像审计失败写成漏洞扫描通过。

两轮 E2E 均设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE=C:\Users\JIE\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe`；根路径使用默认会话文件，子路径另设 `WORKBENCH_E2E_SESSION=../runtime/integration/subpath.local.json` 后执行 `npm run test:e2e`。会话文件不输出或提交。

19:33 的独立复核修复调整权限入口和说明，已执行组件回归、构建及格式检查，并在 6465 最新服务重新通过根路径全部 17 项 E2E。子路径证据对应 19:28 版本；修复不修改 API 前缀或部署路径，未重复构建子路径一轮。

## 用例追踪

| 工作包 | 实现 | 自动化证据 | 结果 |
| --- | --- | --- | --- |
| 身份、项目、空态与权限入口 | api/session/App/LoginView/ProgramsView | api.test.ts、components.test.ts；E2E 登录/项目/刷新 | 通过 |
| 程序发布、任务和运行 | ProgramsView/TasksView/RunsView/RunDetailView | execution.test.ts；E2E ZIP/批次/任务/取消/真实执行/产物 | 通过 |
| 节点凭据与请求竞态 | NodesView/Modal/SecretOnce/composables | execution.test.ts；E2E 节点创建/维护 | 通过 |
| 定时、事件入参和交付授权 | TasksView/SchedulesView/CallersView/BatchesView | scheduling.test.ts、schedules-page.test.ts、input-delivery.test.ts；E2E 标签/调用方/批次 | 通过 |
| 告警处置、通知与人员审计 | AlertsView/PeopleView/AuditView | alerts.test.ts；E2E 真实失败告警/确认/关闭/成员/审计 | 通过；真实消息未发送 |
| 子路径与移动适配 | api/Vite/CSS | API 前缀单测；两轮 E2E，390×844 截图 | 通过 |

## 边界

组件测试隔离了 HTTP，真实浏览器覆盖了上述正常及脚本失败路径；不代表全部后端故障、权限或 PostgreSQL 并发场景都由前端重复验证。原生 Windows 生产身份隔离、Docker 实机发布、真实钉钉、真实 nine 上传、CI、人工评审和生产审批不在本记录中冒称通过。真实浏览器新增记录仅进入已授权的独立测试 schema，由环境维护方统一清理。

Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
