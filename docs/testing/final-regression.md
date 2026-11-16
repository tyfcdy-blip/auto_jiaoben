# 当前开发版本最终回归记录

> 2026-09-17增量：Docker引擎已恢复，本项目已完成真实镜像构建、17项浏览器链路、容器重建及新实例备份恢复。详见[本地Docker验收](../deployment/docker-rehearsal-20260917.md)。下文保留2026-09-16历史结果，不再代表引擎当前不可用。

日期：2026-09-16。本记录是**当前已实现代码**的整体回归，不代表完整系统或原75个设计场景已验收。完整剩余范围见[24个工作包状态](../current-status.md)。

## 一、任务闭环

模式为全过程开发，系统等级L3：涉及权限、持久状态、并发领取和外部业务接口。实际开发及测试限于本机隔离环境、新建PostgreSQL schema、合成文件、无副作用程序。

Git为main且无提交基准，全部新增源码仍未跟踪；未暂存、commit、push、merge、部署或改生产配置。`git diff --check`退出0，但无已跟踪源码基准，不能把它当作全部新增代码已做Git差异审查的证据。另对实际源码执行Ruff、编译、前端类型/构建/格式与独立实现复核。

实现位置：backend/控制服务，frontend/中文管理界面，agent/Windows开发执行器，integrations/nine/离线适配，contracts/机器协议，deploy/部署恢复工具，tests/自动化验证，docs/阶段与运维记录。原nine两份源码最终SHA256与开始记录一致；原数据和部署参考项目未修改。

需求—版本—工作包—实现—用例—结果链接：

- [实施契约](../implementation-contract.md)、[工作包状态](../current-status.md)明确已实现与未完成。
- [测试计划](plan.md)给出数据、前置、输入/异常、测试层级和门禁。
- [198项逐函数结果](cases.md)链接真实测试代码及断言；[机器结果摘要](python-results.json)、[源码摘要快照](source-sha256.json)对应最终本地版本，不伪造Git提交。
- [前端记录](../../frontend/VERIFICATION.md)、[nine全平台记录](../nine/live-platform-verification.md)、[部署记录](../deployment/verification.md)保留专项证据。

## 二、测试证据

最终环境：Windows，项目Python3.12虚拟环境，独立PostgreSQL17.11（回环监听），Node24.18.0，Chromium149.0.7827.55。真实服务使用独立integration schema；登录材料和原始JUnit在已忽略的runtime内，报告不记录密码/令牌。

| 层级/命令 | 结果 | 说明 |
| --- | --- | --- |
| `python -m pytest tests -q --junitxml=runtime/verification/final-python.xml` | 退出0；198通过，0失败，0跳过；88.31秒 | 最后一轮使用真实PG及已启动服务，无跳过PG或nine用例 |
| 执行器tests/agent | 64通过 | 真实进程、Windows Job、日志/状态恢复、同源/路径边界 |
| 后端tests/backend | 51通过 | 权限/存储/两种调度/真实锁并发/提交可见性/告警生命周期 |
| 部署tests/deployment | 24通过 | 含6个真实PG禁派发SQL用例；不等于Docker构建或完整恢复 |
| 实际服务tests/integration | 6通过 | 4个通用执行器及2个nine原程序离线闭环 |
| 独立监控tests/monitor | 29通过 | 实际本机HTTP及独立CLI、停机检测、状态/预算/文件锁 |
| nine本地tests/nine | 17通过 | 真实原程序、合成Excel/异常数据、禁止执行上传 |
| 独立审查tests/review | 7通过 | 撤权窗口、项目边界、触发顺序、注销/节点撤权 |
| `python -m ruff check backend agent integrations deploy tests` | 退出0 | 最终源码静态检查 |
| `python -m compileall -q backend agent deploy tests integrations/nine/adapter.py integrations/nine/legacy_launcher.py integrations/nine/package.py` | 退出0 | Python语法编译 |
| `python -m pip check` | 退出0 | 开发依赖一致性；不是漏洞扫描 |
| `npm test`（frontend） | 退出0；28通过，0失败，0跳过 | 6文件，含6种跨项目运行按钮权限组合 |
| `npm run build`、`npm run format:check` | 分别退出0 | 类型检查、生产构建、格式；主agent最终独立复跑 |
| `npm run test:e2e` | 最新根路径17通过；错误/警告/API错误均0 | 最终本机6465服务；真实注册、执行、下载、失败告警确认关闭 |
| `/automation`专用构建及E2E | 构建退出0；17通过 | 本机2011，子路径上传/agent/下载真实链路；随后仅运行按钮权限/文案修复未重跑子路径，相关组件及最终根路径已回归 |
| 官方npm生产依赖audit | 退出0，0漏洞 | 不包括dev依赖、Python或镜像；镜像源审计404后改仅该命令访问官方registry |

Python有2条第三方弃用警告：Starlette TestClient对httpx的弃用提示、anyio BlockingPortal别名提示；未隐藏警告，未为了警告擅自升级核心依赖。

最终Python复跑命令（仓库根目录；所有路径都只指向本轮隔离环境）：

```powershell
$env:WORKBENCH_TEST_DATABASE_FILE = 'C:\WINDOWS\TEMP\codex-workbench-pg17-141beea5\connection.local.json'
$env:WB_LIVE_AGENT_SESSION = (Resolve-Path runtime/integration/session.local.json).Path
$env:NINE_SOURCE_DIRECTORY = 'C:\Users\JIE\Desktop\拼多多\nine'
$env:NINE_TEST_PYTHON = (Resolve-Path integrations/nine/.venv/Scripts/python.exe).Path
.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=runtime/verification/final-python.xml
```

该机器的临时路径不是可移植生产配置。其他机器先按[PG测试说明](../deployment/native-postgres-test.md)准备独立测试环境，再明确指定路径。缺少环境会明确跳过部分用例，不能沿用本记录称已验证。

最终前端命令在frontend目录执行：`npm test`、`npm run build`、`npm run format:check`；E2E浏览器路径/根路径/子路径完整命令见前端验证记录。真实浏览器和核心业务执行没有被Mock；Mock只隔离钉钉及不能运行的Docker进程边界。

## 三、问题、修复和残余风险

| 等级 | 已发现问题 | 修复与复验 |
| --- | --- | --- |
| P1 | 领取后取消/撤权仍可能启动业务 | started前核验状态与权限；启动门未打开即拒绝；真实PG回归 |
| P1 | 冲突终态报告抛409导致证据回滚 | 提交晚到证据后返回409，不覆盖终态；回归通过 |
| P1 | 异步上传入口同步等数据库锁阻塞事件循环 | 阻塞处理移入线程；PG实际锁等待及健康请求并行测试通过 |
| P1 | HTTP成功响应先于事务提交，立即查询偶发404 | function作用域事务先提交；真实网络提交屏障/失败回滚测试及浏览器复验通过 |
| P1 | 慢上传或等锁期间身份撤销仍沿用缓存 | 所有鉴权写入统一在取得锁后刷新身份/会话；7项独立复验纳入总回归 |
| P1 | 旧备份恢复保留可派发标志，仅靠提醒不可靠 | 恢复后先落库关闭Gate再恢复文件；真实PG拒绝未知结构及失败顺序测试通过 |
| P2 | 项目选项后端不分页导致大于200条重复第一页 | 修复真实分页；205项目的两页不重叠测试通过 |
| P2 | 详情按钮误用所选项目权限，漏项目管理员核对 | 按run所属项目授权显示；6种角色组合和最终E2E通过 |
| P2 | 上传丢响应后无法安全确认 | 新增批次明细查询及同名同内容上传幂等；修改内容仍拒绝；API回归通过 |

当前已实现范围未发现未关闭的可证实P0/P1；这不证明完整系统安全性。以下是明确未完成或未验证的发布阻断项：

- Docker Desktop初始化内部组件失败；没有可用容器引擎。实际镜像构建、非root挂载权限、容器持久化、Linux执行和完整备份恢复均未验证。
- 正式OS任务身份/ACL、业务连接凭据注入、全局物理资源和业务授权确认尚未开发完成；development节点不是安全沙箱。当前代码拒绝启用副作用任务。
- 自动归档补传、计划完整修订/补跑、告警负责人/升级、保留清理、数据迁移及受控恢复启用仍有待开发部分，详见逐包表。
- 真实钉钉、影刀、nine正式上传与部分完成恢复未联调。未获实际测试群/目标系统/业务确认口径，不发送真实消息或写真实业务。
- 并发/数据量/告警时效及RPO/RTO验收阈值未冻结，未宣称容量达标；源代码/依赖摘要与npm部分审计不等于完整依赖/镜像漏洞及许可证门禁。

原nine预检对部分非法金额/日期仍可能返回成功，是已证实的既有业务限制；没有擅改原逻辑，界面结果标明“结构预检、未上传”。

## 四、质量状态

| 项目 | 当前状态 |
| --- | --- |
| Codex开发完成 | 当前已列切片完成；完整24包系统未完成 |
| 测试计划/用例已生成 | 是，当前实现对应计划、可执行用例及逐项结果已记录 |
| 自动化测试已执行 | 是，198 Python＋28前端，及最新17项真实浏览器流程通过 |
| 人工验证已执行 | 否；自动浏览器/AI截图检查不是人工业务验收 |
| 剩余未验证项 | 以上Docker/正式身份/真实集成/迁移恢复/容量等，不省略 |
| 独立复核通过 | 已实现范围的专项复核及修复复验通过；不是完整系统专项人工评审 |
| CI通过 | 未执行 |
| 开发最终确认 | 用户/开发人员尚未确认 |
| 人工代码评审通过 | 未执行 |
| 允许发布 | 否 |

AI自检：**当前开发切片有条件通过；完整系统交付与发布阻断**。下一门禁是完成剩余正式能力与明确测试环境/业务规则，执行Docker及真实系统验收，再做全范围回归、独立专项复核与最终确认。

Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
