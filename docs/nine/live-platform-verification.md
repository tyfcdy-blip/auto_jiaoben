# nine 离线全平台联调实际证据

执行日期：2026-09-16。模式：独立集成验证；按 L3 权限、原脚本和跨进程执行边界验证。只新增测试与本文档，不改平台实现、适配器或原 nine 项目。

## 范围与前置条件

使用已有本机合成联调服务，真实 HTTP、PostgreSQL 独立测试 schema、后台 worker 和执行器 CLI。服务会话由 `runtime/integration/session.local.json` 显式指定；测试不输出该文件的凭据或数据库地址。每次创建独立名称项目，正常结束或测试失败后的清理阶段均停用自己的任务、调用方及节点，保留执行记录；不修改他人项目。

仅通过 `integrations/nine/package.py` 精确读取原目录中两个 Python 源码并打包，检查 ZIP 恰好包含 7 个约定文件及源码摘要。未读取原项目的账单、账号映射、业务配置或历史输出。输入为 `tests/nine/synthetic_workbooks.py` 创建的 3 份合成 Excel；执行别名 `python-nine` 指向独立的 `integrations/nine/.venv` Python。

## 用例与实际结果

| 编号 | 实际链路与断言 | 结果 |
| --- | --- | --- |
| NINE-LIVE-01 | 真实管理员建立并发布程序；授权的合成上游调用方上传 3 份输入、校验 SHA256、冻结批次、触发 check；同事件重发返回同一运行；真实 CLI 领取并执行原核对脚本；平台记录成功、日志和产物完整；下载两份 Excel 核验商家实收 130、推广 -8.5、评价有礼 -2、资金流水合计收入 130/支出 -6。 | 通过 |
| NINE-LIVE-02 | 同一发布版本、同一输入批次，以 preflight 操作触发；重复事件不新建执行；真实 CLI 执行原预检脚本；平台返回成功并可下载清单；清单 execute=false、输入账单 2 份、实际预检 22 行、结果明确“未上传”。 | 通过 |
| NINE-LIVE-03 | 两次运行均验证原合成文件与执行器落地输入摘要不变；network_allowed=false；不存在请求输出和上传 ID 状态文件；该项目无通知通道；读取独立 schema 的 loop_status，确认 scheduler/reconciler/notifier 在任务执行期间持续推进。 | 通过（由以上两个参数化用例共同执行） |

测试代码：`tests/integration/test_live_nine.py`。合成文件生成和报告解析是真实 openpyxl 操作；没有 Mock 核对计算、API、数据库、执行器或产物传输。

实际执行命令（在仓库根目录，环境变量仅为本机显式输入路径）：

```powershell
$env:WB_LIVE_AGENT_SESSION = (Resolve-Path runtime/integration/session.local.json).Path
$env:NINE_SOURCE_DIRECTORY = 'C:/Users/JIE/Desktop/拼多多/nine'
$env:NINE_TEST_PYTHON = (Resolve-Path integrations/nine/.venv/Scripts/python.exe).Path
.venv/Scripts/python.exe -m pytest tests/integration/test_live_nine.py -q -x
.venv/Scripts/python.exe -m ruff check tests/integration/test_live_nine.py
```

最终版本执行结果：pytest **2 passed in 6.30s，失败 0、跳过 0、退出码 0**；Ruff **All checks passed，退出码 0**。首轮在增加后台循环推进断言前为 2 passed in 5.93s；最终结论仅采用上述最终版本结果。未设置三个显式环境变量时，测试主动跳过，不自动读取个人目录或启动服务。

## 边界与未验证项

- 原脚本通过打包的离线启动器执行，启动器安装 Python 审计钩子禁止 socket 与额外子进程；适配器只允许 check/preflight，拒绝正式上传参数。此次未触发真实外部请求、未传入正式上传开关、未使用真实业务凭据。此证据是受控可信脚本的离线执行与运行产物验证，未执行系统层网络抓包，也不代表恶意代码隔离能力。
- 上游由真实 HTTP 调用方测试代码模拟交付动作；**真实影刀流程仍未验证**。原接口连通、真实金额口径、正式上传、请求幂等和远端对账均**未验证**。
- 此处验证事件触发与后台循环推进，**没有声明 nine 的自然定时触发已验证**；平台定时机制另见通用执行器联调用例。
- 原 preflight 仅作结构预检，不等于金额和日期逐字段校验；此次保留适配器的明确提示。生产执行身份、常驻部署和独立 OS 账号隔离未在本轮验证。
- 本轮没有修改生产代码，不涉及数据库迁移。回滚只需撤销新增测试/证据文件；联调记录保留，触发入口已停用。

## 质量状态

本工作包测试代码完成、测试计划与用例已生成、自动化测试已执行，两个关键离线链路通过，未发现本工作包新 P0/P1。AI 自检结论：**有条件通过（限离线合成输入范围）**。人工验证、人工代码评审、CI、开发最终确认和发布审批均未执行；本文由执行该独立测试的子代理编写，最终证据整合和代码独立复核由主线负责，不冒称已通过。

Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
