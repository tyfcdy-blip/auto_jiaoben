# 开发版本测试计划与验收边界

模式：全过程开发。系统整体 L3，涉及账号授权、并发调度、持久状态和外部业务；本轮只在新建隔离库、合成项目和无副作用程序中开发验证。Git main 尚无提交基准，不执行 commit/push/部署。

## 数据、环境和可重复性

- PostgreSQL 17.11 仅监听本机，API测试逐例新建随机 schema，退出清理该 schema；不访问已有业务库。SQLite只适合单元，不替代并发门禁。
- 真实HTTP联调使用独立 integration schema，随机管理员及节点令牌保存在忽略的本地文件。每次测试另建合成项目，结束停用自己的入口，保留运行审计。
- 执行器测试启动真实 Python/子进程树，测试输出只进临时目录。Windows Job 实测，Linux 未验证。
- nine 只复制用户指定的两份源码，并用测试创建的工作簿；隔离 openpyxl 环境，启动前禁止 execute，审计钩子禁止意外网络。原目录、真实账单及凭据不变。
- 钉钉仅以 HTTP Mock 隔离外部发送边界；业务告警记录、投递状态与重试预算用真实实现。独立监控另有真实本机HTTP停机验证。

## 需求—工作包—可执行用例

以下每行是一组验收用例。精确步骤、输入和断言保存在对应测试函数及其 fixture；逐函数执行状态由最终回归清单记录，不把聚合通过替代缺失场景。

| 编号 | 工作包/原设计场景 | 自动化用例位置 | 前置/输入与主要预期 |
| --- | --- | --- | --- |
| T01 | M1.2，A01/A19/A23/A40/P04 | tests/backend/test_v01.py | 新库合成管理员/查看者；登录、退出、限流、停用生效，跨项目与写越权拒绝，秘密不回显 |
| T02 | M1.3，A02/A24/D01 | 同上、tests/agent/test_agent.py | 正常/越界/重复路径 ZIP，发布不可变，运行时映射必须真实，不执行任意shell |
| T03 | M2.1/M2.3，A08–A11/A28–A30 | tests/backend/test_execution.py、tests/agent/test_agent.py | 真实进程、非零退出、超时、取消、孙进程、启动意图重启；不重复执行，unknown保留锁 |
| T04 | M2.2/M3.1，A15/A25/A31/P03/P09 | tests/backend/test_input_scheduling.py、tests/review/test_input_authority.py | 半完成输入等待、未来标签匹配、同批次事件/定时顺序互换只一run，重复事件冲突，同内容文件重传不覆盖 |
| T05 | M2.4，A29/A33/P06–P08 | tests/backend/test_execution.py、tests/agent/test_agent.py | 编号错配、缺结果、错误报告、日志截断、产物缺失/归档异常、终态重复/冲突留证 |
| T06 | M2/M3，A19/A23/A40 | tests/review/test_input_authority.py | 已鉴权后再撤权、注销或停用，真实事务重核验；越权请求不落文件/不解锁业务启动 |
| T07 | M3.1–M3.4，A03–A07/A27/A30/A41 | tests/backend/test_scheduling_notifications.py | 持久计划游标、时区/跨月参数、skip/once、满队列不推进、12并发受理唯一、6节点共享资源只一占有 |
| T08 | M2/M3并发回归 | tests/backend/test_upload_concurrency.py、test_commit_visibility.py | PG真实锁等待不阻塞异步健康请求；真实网络202在提交后发送，提交失败不提前成功 |
| T09 | M4.1/M4.2，A12–A14/A34/P01 | tests/backend/test_alert_lifecycle.py、test_scheduling_notifications.py | 故障入库/通知有限重试、维护抑制、恢复不同于成功，确认与关闭审计，不重跑业务 |
| T10 | M4.3，A22/A34/DEP08 | tests/monitor/test_monitor.py | 主服务停机/503/超时，独立CLI不依赖DB，持久去重、预算、恢复与重叠监控锁 |
| T11 | M5.1，A16/D01–D08/D12 | tests/nine/test_original_programs.py | 原报表合成金额结果、原预检结构结果；空表/坏金额日期事实如实披露，上传及网络拒绝 |
| T12 | M2–M5跨阶段 | tests/integration/test_live_agent.py、test_live_nine.py | 真实服务/PG/worker/agent CLI；事件/定时带参，失败/取消/产物；nine完整离线运行 |
| T13 | M1/M6，DEP02–DEP05/DEP09/DEP10 | tests/deployment/ | Compose解析、秘密/路径、备份恢复安全顺序；真实PG恢复保护SQL关闭派发；不是容器恢复验收 |
| T14 | 前端横跨M1–M4 | frontend/tests/、frontend/e2e/smoke.mjs | 组件加载/错误/权限/提交/竞态；真实浏览器根路径和子路径登记→执行→日志→下载→告警关闭 |

## 准入、退出和未执行门禁

编码切片先确定协议/状态/测试输入，再实现并运行相关用例；完成相关构建、异常回归与独立复核后才能报告该切片通过。跨模块最终回归覆盖全部当前代码，并记录失败、修复和复验。

任何 P0/P1 或关键测试失败禁止发布。原75个设计场景大多跨工作包，不自动等同于上述自动化函数数量。未达到完整场景的剩余部分见当前工作包状态；不将部分实现勾成整场景通过。

当前仍需独立 Docker 引擎实测镜像/卷/容器/恢复；正式任务OS身份与凭据注入、全局物理资源/业务授权、归档补传、迁移、完整告警交接升级仍需开发/验证；真实影刀、钉钉测试群与目标上传系统需现场事实和明确测试权限；容量、保留时间、RPO/RTO需业务及运维阈值。上述均不通过Mock代替。

CI、人工代码评审、业务确认、正式发布分别记录，不能推测。Codex 自检通过不等于开发自测确认、人工代码评审、CI 验证或发布审批通过。
