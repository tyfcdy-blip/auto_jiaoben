# 当前执行状态与事务边界

状态：v0.1实现对应说明；正式副作用能力及未完成门禁见实施进度，不把此表当作生产验收。

| 原状态 | 事件 | 新状态与约束 | 可信证据 |
| --- | --- | --- | --- |
| 无运行 | 事件或定时受理 | queued 或 waiting_input | 参数/版本/批次快照、TriggerEvent和Run在同一事务提交，提交完成才发送受理响应 |
| waiting_input | 明确批次ready | queued | 输入被冻结，文件元数据完整；超时failed且不产生进程 |
| queued | 节点领取 | leased | gate行锁下检查授权、能力、槽位，原子保存attempt和完整资源集合 |
| leased | started握手 | running | 本机启动意图已持久化、受控启动器PID已登记；复查取消/授权后才解锁业务入口 |
| queued/waiting_input | 人员取消 | canceled | 无进程启动，无业务结果伪造 |
| leased/running | 人员取消 | cancel_requested | 仅发送停止意图；未收到进程证据不释放危险资源 |
| running/cancel_requested | 进程终止与结果 | succeeded/failed/partial/canceled/unknown | 实际退出码、匹配身份的结果及观测分开保存；错误报告不得被可选结果模式忽略 |
| leased/running/cancel_requested | 节点失联或停止超时 | unknown | 不换节点重跑，不因租约到期自动释放资源 |
| unknown | 迟到结果 | 保持unknown | 存储late_evidence，由授权人员核对，不能自动消除不确定性 |
| 任意已有终态 | 不同内容的完成报告 | 保留原终态，返回409 | 冲突证据先持久化再返回，不通过抛事务异常把证据回滚 |
| unknown | 授权人工核对 | succeeded/failed/canceled | 必需核对说明与审计；不伪造原退出码，也不声称外部事务已回滚 |

终态、日志完整性、归档状态和告警投递分别保存。对外展示succeeded不表示群消息已被阅读。通知有限重试不重新受理业务任务。

当前用数据库单行锁将调度控制写入串行化，便于一期证明一致性；不宣称已达高吞吐。读请求不持该锁。上传读请求体采用限额流式读，完整同步处理放入线程池，避免事件循环等待数据库锁。将来优化锁粒度必须重新验证并发场景。

依据：FastAPI的yield依赖默认在响应后退出，因此统一采用function scope让数据库事务在响应发出前提交。参见[官方依赖生命周期说明](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#early-exit-and-scope)。正常响应与事务提交顺序由真实网络测试验证，不能仅看TestClient测试通过。
