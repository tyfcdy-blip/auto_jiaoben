# 接入简化与界面改版：验收记录

日期：2026-09-17。执行模式：全过程开发；本次 L2（跨前端与 Agent 的接入改造），整体系统仍按 L3 生产门禁管理。计划先于修改建立，见 [实施及验证计划](usability-redesign-plan.md)。

## 任务闭环

| 工作包 | 实际交付位置 | 主要验证 | 状态 |
| --- | --- | --- | --- |
| UX01 程序连续接入 | frontend/src/views/ProgramsView.vue、components/ProgramImport.vue | program-import.test.ts 11项；真实浏览器上传/发布/历史版本 | 完成 |
| UX02 执行电脑连接 | NodesView、NodeConnectionForm、nodeConnection.ts；agent/connection.py、__main__.py | nodes.test.ts 10项；test_connection.py 17项；真实下载→导入→运行 | 完成 |
| UX03 文件一次交付 | BatchesView、batchDelivery.ts | batches-delivery.test.ts 20项；真实两文件交付/详情 | 完成 |
| UX04 视觉及日常任务 | App、OverviewView、LoginView、WorkbenchIcon、style.css、forms.css、TasksView | 视觉专项、任务默认版本/迟到响应；桌面/手机截图及可点击操作 | 完成 |
| UX05 联合验证 | tests、frontend/e2e/smoke.mjs、Docker开发实例 | 下表实际执行证据与独立复核 | 本轮通过，生产边界未关闭 |

Git基准为main未提交仓库，既有源码全部未跟踪；没有可引用的HEAD提交，因此不将空git diff冒称为本轮无差异。未暂存、提交、推送或更换分支。未修改后端协议、数据库结构、权限或调度规则，也没有修改原nine项目。测试脚本和本轮源码摘要另存 [source-sha256.json](usability-evidence/source-sha256.json)。

## 测试证据

| 层级 | 命令/环境 | 实际结果 |
| --- | --- | --- |
| Python完整回归 | `.venv/Scripts/python.exe -m pytest -q --junitxml=runtime/usability-python-results.xml`；配置本地PG、联调会话、nine隔离运行时 | 215通过、0失败、0跳过，95.79秒，退出0；2条现有第三方弃用警告 |
| 前端组件/故障边界 | 在frontend执行 `npm test` | 10文件、75通过、0失败，退出0 |
| 类型与构建 | `npm run build` | TypeScript/Vite成功，81模块，退出0 |
| 静态检查 | `npm run format:check`；`.venv/Scripts/python.exe -m ruff check backend agent tests integrations` | 退出0 |
| 本地真实UI/Agent | `npm run test:e2e`，默认本机6465会话 | 17项通过，退出0，浏览器错误/警告/API错误均0 |
| Docker构建 | `docker compose --env-file deploy/.env.example -f deploy/compose.yaml build api` | 退出0，沿用既有ECR基础镜像参数和依赖，无全局Docker变更 |
| Docker运行更新 | `docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --no-deps --no-build api worker` | 退出0；只重建本机开发API/worker，不运行迁移，不替换数据库和秘密 |
| Docker真实UI/Agent | E2E的 `WORKBENCH_E2E_SESSION` 指向 `runtime/docker/session.local.json` | 17项通过，退出0，浏览器错误/警告/API错误均0；API和数据库健康，worker运行中 |

Python首次回归仅配置数据库，得到191通过/23因未指定联调或nine环境而跳过；随后明确指定既有隔离环境并完整重跑，最终215项全过。未通过删除测试或扩大跳过获得成功。

复现Python完整回归的环境变量：

```powershell
$pointer = Get-Content runtime/postgres17/instance.local.json | ConvertFrom-Json
$env:WORKBENCH_TEST_DATABASE_FILE = $pointer.connection_file
$env:WB_LIVE_AGENT_SESSION = (Resolve-Path runtime/integration/session.local.json).Path
$env:NINE_SOURCE_DIRECTORY = 'C:\Users\JIE\Desktop\拼多多\nine'
$env:NINE_TEST_PYTHON = (Resolve-Path integrations/nine/.venv/Scripts/python.exe).Path
.venv/Scripts/python.exe -m pytest -q --junitxml=runtime/usability-python-results.xml
```

环境：Windows、Python3.12、本地PostgreSQL17、Node24.18、Chromium149.0.7827.55；Docker构建Node22/Python3.12。截图检查1440×1000桌面和390×844手机。所有业务数据为可识别合成数据；nine原脚本相关测试只使用既有授权源码及隔离合成输入，不发送真实外部业务请求。

HTTP模拟仅用于组件的网络失败、坏响应和迟到响应测试；真实浏览器链路使用实际PostgreSQL、HTTP接口、下载的连接文件、真实Agent子进程、合成脚本、日志与产物。钉钉不真实发送。

持久证据：[本地浏览器结果](usability-evidence/native-browser.json)、[Docker浏览器结果](usability-evidence/docker-browser.json)、[Python JUnit](usability-evidence/python-results.xml)、[首页截图](usability-evidence/02-overview-desktop.png)、[手机任务截图](usability-evidence/04-tasks-mobile.png)、[文件详情截图](usability-evidence/09-input-files-desktop.png)。

本轮Docker镜像ID：`sha256:b99eb5cfc6cf5b45566f83f45bd72112e73ed3fded947f7c5cb425b591283b22`。未推送镜像仓库。只更新开发实例，未执行生产部署。此前9月17日备份恢复演练另有记录，本轮不冒称再次完成迁移或灾备演练。

## 缺陷、修复与剩余风险

| 等级 | 问题 | 修复及复验 |
| --- | --- | --- |
| P1，已关闭 | 登记返回损坏JSON/缺少标识或token时，不能确认保存却可能重复创建 | 程序、电脑、批次逐项校验响应；非明确4xx拒绝按结果不明处理，禁止盲目再创建；专项坏响应测试通过 |
| P2，已关闭 | 批次详情原先不读取真实文件清单 | 使用详情接口；重新打开已交付批次真实核对文件 |
| P2，已关闭 | 更新跨分页批次导致总数虚增 | 只更新当前页已有行，列表独立刷新；分页及刷新失败用例通过 |
| P2，已关闭 | Tasks新增作用域防护缺少直接测试 | 增加项目切换/卸载后迟到响应测试；独立复核确认覆盖缺口关闭 |
| P2，已关闭 | 手机任务操作在表格可视区域外 | 手机隐藏技术列，保留名称/状态/操作；真实浏览器验证按钮在视口内且可打开运行窗口 |
| P2，已关闭 | 新表单HTML pattern在Chromium的Unicode Sets模式报错 | 转义短横线；完整E2E保留无控制台错误断言并复验通过 |
| 构建缺陷，已关闭 | 多语句模板事件经格式化后解析失败 | 改为命名事件处理函数，构建及E2E通过 |

当前无已发现且未关闭的本轮P0/P1。程序列表仍依赖后端版本接口已有200条上限；超大版本库的完整检索不属于本轮交付。ZIP仍需遵循接入规范，运行环境仍需准备。连接导入不自动安装或常驻启动。正式OS隔离、业务凭据、真实影刀/钉钉/nine写入、生产HTTPS、容量保留、升级/异机恢复尚未完成或未验证；不能把此轮界面和开发镜像判为生产可用。

回退边界：后端及存储模型未改变，可回到已留存的上一个开发镜像；没有本轮数据库逆向迁移。Agent保留原 `--config`，导入只新建指定配置目录，不覆盖已有配置。创建和已发布的测试记录未执行破坏性删除，保留在各自隔离实例中。没有执行一次新的回滚演练。

## 质量状态

| 项目 | 状态 |
| --- | --- |
| Codex开发完成 | 本轮范围完成 |
| 测试计划/用例已生成 | 是 |
| 自动化测试已执行 | 是，结果见上表 |
| 人工验证已执行 | 否；仅Codex实际浏览器操作与截图检查 |
| 剩余未验证项 | 生产环境、正式执行身份/业务凭据、真实外部系统、容量/升级恢复等 |
| 独立复核通过 | 本轮代码复核通过；分别审查视觉、连续接入、文件交付、连接导入与异常响应修复 |
| CI通过 | 未运行CI |
| 开发最终确认 | 待用户确认 |
| 人工代码评审通过 | 未执行 |
| 允许发布 | 不允许据此发布生产；本机开发实例已更新 |

AI自检结论：本轮改版有条件通过，可在开发环境继续联调；生产状态不通过。下一门禁：首期生产范围确认、正式执行与业务接入实现及真实授权验收。

Codex自检通过不等于开发自测确认、人工代码评审、CI验证或发布审批通过。
