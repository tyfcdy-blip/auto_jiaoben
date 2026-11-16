# 文件夹输入：开发与验收记录

日期：2026-09-17。模式：全过程开发。等级 L3（文件访问边界、跨模块执行和调度）。用户明确选择当前电脑首轮测试；仅合成文件、本机独立 PostgreSQL schema 和本机 Docker 开发实例，无真实业务写入、消息发送、生产部署或数据库结构变更。计划见 [folder-input-plan.md](folder-input-plan.md)。

## 任务闭环

Git 基准 main 尚无 HEAD 提交，项目源码原已全部未跟踪；不把空 git diff 当作没有修改。无暂存、commit、push。改动限定本轮功能、示例、测试、接入文档及新增测试产物忽略项。保留既有数据、原 nine 项目和部署参考资料。

| 工作包 | 实现位置 | 用例与验收 | 状态 |
| --- | --- | --- | --- |
| DIR01 目录声明及双触发 | backend/workbench/input_sources.py；catalog、operations、execution、agent routes及storage | tests/backend/test_folder_inputs.py：22项，真实PG，固定节点/能力、路径、参数、互斥、事件/业务去重、计划日期 | 完成 |
| DIR02 执行电脑目录读取 | agent/directory_input.py、config.py、connection.py、runner.py；claim.schema.json | test_directory_input.py：25项；原执行器/连接回归；新增3个真实CLI不可达代理场景 | 完成 |
| DIR03 目录界面 | directoryInput.ts、DirectoryFields.vue、ProgramImport、TasksView、NodeConnectionForm、nodeConnection及types | directory-input.test.ts：48项；目录路径/节点能力、表单、参数和旧模式兼容；7项真实目录UI | 完成 |
| DIR04 四类合成程序 | examples/folder-jobs；tests/folder_jobs | 19项真实Python/Node子进程测试，输入不变、结果、异常、取消与超时 | 完成 |
| DIR05 全链路和交付 | tests/integration/folder_acceptance.py、frontend/e2e/folder-input.mjs、指南及本记录 | 以下完整回归、两套实例真实执行、Docker及独立复核 | 本机测试范围完成 |

真实目录执行用例共11项：Python递归合并及产物下载；同一事件重发去重；Node统计；主动失败及日志；真实超时终止；缺失/空/越界目录各1项；平台告警生成；真实时钟定时触发并解析日期；全部输入SHA256保持不变。每项实际证据及run_id见 [原生结果](folder-evidence/native-acceptance.json)、[Docker结果](folder-evidence/docker-acceptance.json)。任务、计划、调用方都属于本轮单独项目，计划验收后关闭，模拟调用方禁用。

## 测试证据

| 层级 | 实际命令与环境 | 结果 |
| --- | --- | --- |
| Python完整回归 | `.venv/Scripts/python.exe -m pytest -q --junitxml=runtime/folder-python-results.xml`，环境配置见下 | **284通过、0失败、0跳过**，112.72秒，退出0 |
| 前端完整组件回归 | frontend目录 `npm test -- --reporter=default --reporter=json --outputFile=../runtime/folder-frontend-results.json` | **123通过、0失败、0跳过**，11文件，1.99秒，退出0 |
| 静态及构建 | `.venv/Scripts/python.exe -m ruff check backend agent tests integrations examples/folder-jobs`；frontend `npm run format:check`、`npm run build` | 退出0；TypeScript和Vite成功，84模块 |
| 原生真实目录 | `.venv/Scripts/python.exe tests/integration/folder_acceptance.py --session-file runtime/folder-input-session.local.json --output runtime/folder-acceptance/native-02` | **11通过**，退出0，真实HTTP/PG/业务进程/时钟；运行49.03秒 |
| Docker真实目录 | 同脚本，session-file改为runtime/docker/session.local.json，output为runtime/folder-acceptance/docker-01 | **11通过**，退出0，Windows读取本地目录+Linux容器控制面 |
| 常规真实UI回归 | frontend `npm run test:e2e`，分别指定原生和Docker会话 | 两套各 **17通过**，退出0，无浏览器错误、警告和API错误 |
| 新目录真实UI | frontend `node e2e/folder-input.mjs`，分别指定原生和Docker会话及对应目录验收证据 | 两套各 **7通过**，退出0，无浏览器错误、警告和API错误 |
| Docker本机开发更新 | `docker compose --env-file deploy/.env.example -f deploy/compose.yaml build api`；`docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --no-deps --no-build api worker` | 退出0，API/数据库healthy，worker运行；不运行初始化或迁移，不变更全局代理 |
| 测试交付状态 | 登录真实Docker API核对本次项目节点/任务/计划/调用方 | 节点在线、4个目录任务、2个计划均关闭、1个调用方禁用；见ready-state.json |

Python完整回归的进程级环境设置：

```powershell
$pointer = Get-Content runtime/postgres17/instance.local.json | ConvertFrom-Json
$env:WORKBENCH_TEST_DATABASE_FILE = $pointer.connection_file
$env:WB_LIVE_AGENT_SESSION = (Resolve-Path runtime/folder-input-session.local.json).Path
$env:NINE_SOURCE_DIRECTORY = 'C:\Users\JIE\Desktop\拼多多\nine'
$env:NINE_TEST_PYTHON = (Resolve-Path integrations/nine/.venv/Scripts/python.exe).Path
$env:NO_PROXY = '127.0.0.1,localhost,::1'
```

UI设置 WORKBENCH_E2E_SESSION 为上述对应会话文件绝对路径，PLAYWRIGHT_CHROMIUM_EXECUTABLE 指向本机已有 `C:\Users\JIE\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe`。目录UI额外设置 WORKBENCH_E2E_ACCEPTANCE 为对应acceptance.json路径。Docker沿用已验证ECR基础镜像参数：WB_NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim，WB_PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm，WB_POSTGRES_IMAGE=public.ecr.aws/docker/library/postgres:17-bookworm。未安装新依赖、未修改Windows或Docker全局代理。

环境：Windows、Python3.12、Node.js、本地PG17，Chromium149.0.7827.55；容器Python3.12/Linux，构建Node22。目录输入都是工具生成的CSV/JSON/TXT；真实程序不调用业务API。单元层HTTP Stub只隔离控制面及异常边界；真实集成没有模拟目录扫描、哈希、数据库、领取、启动、日志、归档或真实定时。钉钉仅验证平台故障记录，本轮没有发送真实群消息。UI受理检查后取消队列，实际进程运行证据由11项目录集成提供。

持久证据：[Python JUnit](folder-evidence/python-results.xml)、[前端结果](folder-evidence/frontend-results.json)、[原生常规UI](folder-evidence/native-browser.json)、[Docker常规UI](folder-evidence/docker-browser.json)、[原生目录UI](folder-evidence/native-folder-browser.json)、[Docker目录UI](folder-evidence/docker-folder-browser.json)、[交付状态](folder-evidence/ready-state.json)、[运行表单截图](folder-evidence/directory-manual-form.png)。源码摘要另存source-sha256.json。Docker镜像ID为 `sha256:ad0321605943a7cffc4dc52d949f057852f7af33d1b73f37e875a1af6955fee8`，未推送。

## 缺陷、复验及风险

| 等级/状态 | 事实与处理 |
| --- | --- |
| P1已关闭，本机连接 | 首次native-01在接口登记阶段遇到502；实际对比trust_env发现Windows系统代理影响回环。Agent回环直连、远程保留原代理行为，合成验收客户端限定本机直连；新增不可达代理下的真实CLI测试3项通过，原生/Docker真实执行均通过。没有变更全局网络设置。首次未形成完整验收，不计通过。 |
| P2已关闭，路径一致性 | 独立审查实测前端接受/data/./month、/data:stream、//single而后端拒绝。同步词法规则和边界用例，主线复核源码及完整123项测试通过。 |
| P2已关闭，电脑选择 | 目录任务曾显示无目录能力电脑；现在仅列支持目录的电脑，切换模式清理不兼容选择、提交再次检查。组件切换/恢复回归和Docker完整UI通过。 |
| 测试脚本已修复 | 首轮目录UI对执行任务标签使用过严exact定位，在计划步骤停止；修正测试定位后原生/Docker各7项全部通过，未修改核心断言或跳测。首次默认浏览器缺失，显式选择本机已装Chromium，无下载。 |
| 环境瞬态，未定位 | 独立后端复核首次18通过及4个TestClient/anyio初始化ERROR，未改代码单项及整组复跑22通过；最终完整284项未复现。保留此事实，不声称已定位或修复。 |
| 非阻断警告 | Python有2条现有Starlette/anyio弃用提示；组件测试有既有精简Router的/runs路由警告。实际浏览器错误/警告为0；没有升级依赖或隐藏失败。 |

独立复核由不同子代理只读检查后端、执行器与前端，主线核验实现及实际完整回归。未发现本轮未关闭P0/P1。前端两项P2已修并复验，主线核对修复。子代理报告不替代人工评审。

仍存在明确边界：直接原目录不冻结，不构成OS沙箱；脚本必须受信任。业务超时不包含目录扫描准备；网络共享单次阻塞IO及准备阶段取消优化待完善（当前取消记startup_failed）。真实UNC权限、共享断网、Linux执行器分支、大数据容量/性能、本地HTTPS使用环境证书文件等未验证；回环trust_env=False同时忽略环境证书设置。当前运行身份和临时Agent进程不等于正式常驻服务。真实影刀、钉钉、nine上传、业务凭据与实际服务器验收仍未完成。下一步首先完成受限执行身份/常驻和真实测试连接，必要配置见[用户指南](../folder-input-guide.md)。

回滚边界：没有数据库迁移，原批次/普通任务接口、原连接文件和运行协议保留兼容。但已有目录任务不可直接交给旧平台：回退前停用目录任务/计划、等待确定终态并保留unknown核对，随后回退API/worker及Agent；不能以旧版不识别目录字段为由继续派发。当前未执行新的版本回滚、生产升级或灾备演练。历史记录均保留，不破坏性清理本次测试数据。

## 质量状态

| 项目 | 状态 |
| --- | --- |
| Codex开发完成 | 本轮目录输入和本机合成测试范围完成 |
| 测试计划/用例已生成 | 是，工作包和测试可追踪 |
| 自动化测试已执行 | 是，实际结果和失败复验见上 |
| 人工验证已执行 | 否；浏览器操作及截图检查由Codex执行 |
| 剩余未验证项 | 上述生产身份、常驻、真实连接、共享网络、容量、升级恢复等 |
| 独立复核通过 | 本轮复核通过，修复后主线复验；不等于人工评审 |
| CI通过 | 未运行CI |
| 开发最终确认 | 待用户确认 |
| 人工代码评审通过 | 未执行 |
| 允许发布 | 仅已更新本机开发实例，不允许据此发布生产 |

AI自检结论：**有条件通过，可开始当前电脑合成测试；不能判定生产可用**。下一门禁为用户试用确认及正式执行/外部连接验收。Codex自检通过不等于开发自测确认、人工代码评审、CI验证或发布审批通过。
