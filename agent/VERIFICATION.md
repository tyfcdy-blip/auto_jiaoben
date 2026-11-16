# 执行器工作包验证记录

日期：2026-09-16。环境：Windows，项目 `.venv`，Python 3.12.7。范围仅为 `agent/`、`contracts/runtime/`、`examples/safe-job/` 和 `tests/agent/`。没有修改后台、前端或部署文件，没有 commit。

## 已实现

- `python -m agent --config <file>` 常驻主动心跳、领取、执行、取消与回报；`--once` 支持本地验收。
- 节点令牌独立文件读取；业务进程使用最小环境白名单，不继承父进程秘密变量或节点令牌。
- 程序包及输入限额、同源下载、SHA256、ZIP 路径/链接/重复文件检查；输入总量受限。
- JSON Schema 校验 claim/context/result；运行时别名、独立目录、非 shell 参数传递、结果归属校验。
- Windows Job 在真实业务进程开始前完成绑定；超时、取消及退出后残留子进程清理。残留子进程会产生明确错误，不仅凭主入口退出码判完整成功。
- 启动意图与终态原子落盘、独占状态锁、启动不确定窗口转 unknown、完成响应丢失后相同载荷重传、不重复启动。
- stdout/stderr 持续并行排空、有界内存及磁盘日志、日志截断/丢失显式记录；日志失败不阻塞业务终态上报。
- 必需/可选产物检查及归档，归档失败显式 incomplete，不因归档或日志问题重跑业务。

## 实际验证

| 命令 | 退出码 | 实际结果 |
| --- | --- | --- |
| `.venv\Scripts\python.exe -m pytest tests/agent -q` | 0 | 最新子路径兼容回归：64 passed，17.92 秒，无失败、无跳过；前序 44 项通过，14.59 秒 |
| `.venv\Scripts\python.exe -m ruff check agent tests/agent examples/safe-job` | 0 | All checks passed |
| `.venv\Scripts\python.exe -m compileall -q agent examples/safe-job` | 0 | 成功 |

上述模块测试使用临时目录、合成 ZIP/输入和无副作用脚本。核心子进程、进程树、文件持久化与 ZIP 校验均实际执行；多数外部 HTTP 边界使用 httpx MockTransport，另有真实 CLI → 本机 ThreadingHTTPServer → 真实业务子进程测试。没有连接 nine 接口、钉钉群或生产系统；真实隔离控制台联调见下节。

子路径修订增加：根路径、单层前缀、嵌套前缀的真实 CLI/HTTP 执行；前缀下输入、日志与产物链路；跨应用前缀、相似前缀、单次/双重编码穿越、路径重复分隔、绝对 URL 和片段拒绝。控制请求与下载均保持在配置源站及应用前缀内。

覆盖正常带参、输入下载、产物上传、非零退出与 stderr、结果缺失/非法/归属错误/超限、必需产物缺失、可选产物失败、日志洪峰、超时及取消杀孙进程、入口提前退出、父环境秘密不继承、终态响应丢失、重复领取、重启未知意图、启动确认响应丢失、同源限制、ZIP 越界/链接/重名/容量、坏 ZIP、校验值不匹配、状态锁、未知运行时、生产模式拒绝、CLI 配置校验。

初轮静态检查发现测试文件一个无用导入，已删除并复验。后续自检补充了非法 ZIP 的受控失败、结果容量、目录身份歧义、日志故障不遮蔽终态及残留子进程核验；最终以上证据覆盖修改后的版本。

## 真实隔离控制台联调

在主代理提供的本机 HTTP 服务与独立 PostgreSQL schema 中运行 `tests/integration/test_live_agent.py`。登录材料由本地受控文件读取，不写入输出或文档。测试另建 `agent-live-` 前缀的独立合成项目，使用真实 HTTP、真实 PostgreSQL、真实 CLI 及真实子进程，没有模拟调度或执行核心逻辑。

实际命令（PowerShell）：先设置 `WB_LIVE_AGENT_SESSION` 指向主代理提供的本机会话文件，再运行 `.venv\Scripts\python.exe -m pytest tests/integration/test_live_agent.py -q -x`。结果：退出码 0，**4 passed in 7.83s**，无失败、无跳过。`ruff check tests/integration/test_live_agent.py` 退出码 0。

| 联调场景 | 实际结果 |
| --- | --- |
| 外部调用方事件入参、重复事件、真实执行、结果及产物下载 | 同一事件复用原 run；真实进程 exit 0；状态 succeeded；日志存在；下载 summary.txt 内容与参数一致 |
| 同一任务的定时入参 | 只修改本测试计划的 next_fire 使其到期，真实 worker 受理，`${scheduled_date}` 按计划时间解析并成功执行；这是显式测试时钟注入，不是自然等待 |
| 真实脚本失败 | exit 9、stderr、failed 和关联告警均可查询；项目未创建通知通道，没有真实群消息 |
| 真实运行中取消 | API 发起取消后 CLI 正常退出，运行归档为 canceled，observation=canceled |

收尾仅禁用本测试创建的节点、任务、计划和调用方，保留审计与运行记录供复核，不改其他项目。集成测试默认不自动运行，必须显式设置本机会话文件；不能对未核实环境执行。该轮为顺序接入验收，不替代 PostgreSQL 并发、部署升级或后台审查修复后的专项测试。

## 边界与未验证

- 当前执行器只开放 development 且仅允许 side_effects=false；同用户开发环境不是安全沙箱。正式 Linux/Windows 独立任务身份与秘密 ACL 未验证，os_user 配置直接拒绝，正式业务上传阻断。
- Windows Job 真实测试通过；POSIX 进程组代码尚未在 Linux 实机执行，不宣称跨平台验收完成。
- 当前串行执行，节点应登记 slots=1。生产 CPU/内存强限制、历史运行保留清理和运维容量验证未完成；磁盘数据不能无期限增长。
- 每次终态发送前固定本次归档观测；归档失败保留本地文件并报告 incomplete，不自动重跑业务。归档补传运维能力仍需后续明确，不能将 incomplete 当成归档成功。
- 独立项目的本机 HTTP/PostgreSQL 顺序联调已完成。PostgreSQL 并发、Docker、远端网络/TLS/凭据撤销、人工及 CI 验证由主线继续完成；本记录不替代全系统验收。

本工作包自检有条件通过。Codex 自检不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
