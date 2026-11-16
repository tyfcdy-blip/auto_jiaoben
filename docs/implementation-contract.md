# 当前开发实施契约（2026-09-17）

这是当前实现共同遵守的开发契约，正式 OpenAPI 从后端模型导出并纳入测试。历史方案说明目标范围，本文件记录当前开发边界，不代表验收通过。

## 技术与小版本

采用 Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 17，Vue 3 + TypeScript + Vite；Python 原生执行器以 HTTP 主动连接。生产禁止 SQLite，SQLite 只用于隔离单元测试。Docker Compose 发布，接口和后台进程使用同一镜像不同命令。基础包版本按本项目锁文件固定，不使用个人全局依赖。

- v0.1：M0/M1，契约、账号/项目授权、版本/任务登记、容器基础。
- v0.2：M2，真实进程执行、输入交付、日志结果和恢复。
- v0.3：M3，带参事件及定时、持久受理、资源互斥。
- v0.4：M4，故障监测、告警投递及独立健康。
- v0.5：M5，nine 报表/预检适配和授权测试上传的独立门禁。
- v1.0 候选：M6，完整回归、容器部署/恢复演练与发布资料；尚无生产发布授权。

每版本在 docs/releases 中记录实现、测试命令及退出码、通过/失败/跳过、遗留和下一门禁。未验证不能标记通过，不以本地 SQLite 测试替代 PostgreSQL 并发验证。

## HTTP 共识

前缀 `/api/v1`。JSON。列表响应 `{items: [...], total: n}`，默认 limit=50 最大200，offset>=0。错误 `{code,message,request_id}`；请求参数严格拒绝未知字段。日期 UTC ISO8601，界面 Asia/Shanghai。

人员登录返回 `{token,user}`，短期会话令牌仅存在浏览器内存，刷新重新登录；Authorization: Bearer。用户字段 id,username,display_name,is_admin,active。项目成员角色 project_admin/developer/operator/viewer。节点与外部调用令牌单独签发、仅返回一次、数据库保存摘要，不与人类会话通用。

人员端点：POST `/auth/login` {username,password}，GET `/auth/me`，POST `/auth/logout`。首次管理员通过 CLI `python -m workbench.cli init-admin` 从环境/交互输入创建，不开放公网首次注册。

管理端点：GET/POST `/users`（POST username,display_name,password,is_admin）；PATCH `/users/{id}`（active）；GET/POST `/projects`（POST name）；GET/POST `/projects/{id}/members`（POST user_id,role）。

程序：GET/POST `/programs`（POST project_id,name,description）；GET `/programs/{id}/versions`；POST `/programs/{id}/versions`（name,command: string[],parameter_schema: object,side_effects: bool,result_required: bool,required_capabilities: string[],protocol_version="1"）。版本入口 command 的第一个参数是节点本地已登记运行时别名（例如 python），后续相对路径从已校验包根解析。命令不经 shell，参数通过 JSON context 文件，不进行字符串模板执行。POST `/versions/{id}/bundle` 上传 ZIP 包原始二进制（application/zip）；上传只允许未封存版本。POST `/versions/{id}/publish` 封存；版本包 SHA256、入口及参数模式不可更改。

任务：GET/POST `/tasks`（POST project_id,name,version_id,default_parameters={},node_id=null,timeout_seconds=300,resource_keys=[],business_key_fields=[],requires_input=false,operation="run"）；PATCH `/tasks/{id}`（enabled）。GET `/tasks/{id}`。版本/operation/资源和副作用绑定任务，外部事件无权覆盖。business_key_fields 只能引用参数模式已声明的字段，运行时必须有值。

输入：POST `/batches` {project_id,label}，PUT `/batches/{id}/files/{filename}` 原始二进制（仅单层安全文件名），POST `/batches/{id}/ready` 冻结清单，GET `/batches`、GET `/batches/{id}`（含文件名称/大小/SHA256）。同项目标签唯一；同名同内容上传可重试，不新增记录，冻结后也只确认原内容；同名不同内容拒绝，修订必须新批次。调用方需明确授予 allow_input_upload 才能交付本项目批次，此权限覆盖整个项目，不限于其 task_ids 或自建批次。冻结输入不可修改；客户端本地路径不等于服务器文件。

运行：POST `/tasks/{id}/runs` {event_id,input_batch_id:null,parameters:{}}；初次202，重复同内容200返回 `{run_id,accepted:true,deduplicated,state}`，event_id在调用身份范围唯一（跨任务同ID冲突409）。GET `/runs`、GET `/runs/{id}`（含 attempt_id,task_name,state,parameters,result,exit_code,observation,error,artifact_state,created_at,started_at,finished_at,node_id）；GET `/runs/{id}/logs` 返回有界 entries（sequence,stream,text）；POST `/runs/{id}/cancel`；POST `/runs/{id}/reconcile` {outcome:"failed"|"succeeded"|"canceled",note} 仅管理员且运行unknown，必须提供核对说明，记录人工核对而非伪造退出证据。

节点：GET/POST `/nodes`（POST name,project_id,capabilities:[],slots:1，返回 id/token）；PATCH `/nodes/{id}` {enabled,maintenance}。列表有 last_seen,online,capabilities,slots,isolation。外部调用方 GET/POST `/callers`（POST name,project_id,task_ids:[],allow_input_upload:false），PATCH `/callers/{id}` {active}，返回 id/token 仅创建时。

计划：GET/POST `/schedules`（task_id,name,cron,timezone="Asia/Shanghai",parameters:{},input_batch_id:null,input_label:null,misfire_policy:"skip"|"once",enabled:false），PATCH `/schedules/{id}` {enabled}。固定批次与标签二选一；标签可引用日期白名单，批次尚不存在时生成 waiting_input 运行，待同项目同标签批次冻结再排队。事件与计划引用同业务批次时去重，不分别执行两遍。一期限定5字段 cron；日期表达式只接受完整字符串 `${scheduled_date}` `${previous_month}`，不支持拼接，禁止 eval；从计划时刻而非启动时间解析。默认新计划关闭，由用户明确启用。

告警：GET `/alerts`、POST `/alerts/{id}/ack` {note}、POST `/alerts/{id}/close` {note}。确认仅限open；关闭幂等且审计。节点恢复或离开排队条件时生成独立恢复事件，原告警转recovered，不把unknown运行改成成功；维护节点抑制新的离线告警。GET/POST `/channels`（project_id,name,webhook_url,signing_secret 可空；秘密只写不读），PATCH `/channels/{id}` {enabled}。平台负责人配置钉钉机器人适配，程序没有告警 API。

总览 GET `/overview`，审计 GET `/audit`，健康 GET `/health/live`、`/health/ready`；运维探针 `/health/components` 需要单独监控令牌，不泄露业务数据。

## 节点协议 v1

所有接口使用节点 Bearer；领取归属必须匹配当前 node/attempt，不允许任务篡改其他运行。

- POST `/agent/heartbeat` {capabilities:[],isolation:"development"|"os_user",agent_version:"0.1.0"}，返回 `{cancel_run_ids:[]}`。
- POST `/agent/claim` {}，无任务204；有任务200返回 `{run_id,attempt_id,protocol_version:"1",command:[],bundle_sha256,bundle_url,parameters:{},operation,timeout_seconds,side_effects,result_required,input_files:[{name,sha256,url}],resource_keys:[]}`。URL 是相对 `/api/v1/...`，节点通过认证下载。
- POST `/agent/runs/{id}/started` {attempt_id,pid}；必须先在本机持久记录启动意图再创建进程；意图未确认或重启后进程状态不明，不重复执行。
- POST `/agent/runs/{id}/logs` {attempt_id,entries:[{sequence,stream:"stdout"|"stderr"|"system",text}]}；序号单调，重复内容幂等，最多100条，每条最多4096字符。
- POST `/agent/runs/{id}/complete` {attempt_id,exit_code:null|int,result:null|object,error:null|string,observation:"exited"|"timeout"|"canceled"|"startup_failed"|"unknown",artifact_state:"none"|"complete"|"incomplete"}。重复回报同内容可接受，冲突回报不覆盖终态。unknown 保留资源，停止/退出与业务结果分别存证。
- GET `/agent/runs/{id}/control?attempt_id=...` 返回 `{cancel_requested:bool}`。任务超时按领取 timeout 执行，平台离线不使执行器重复启动。
- 归档 POST `/agent/runs/{id}/artifacts/{filename}?attempt_id=...` 原始二进制；文件名安全、配额、节点运行归属校验；下载走授权人员接口。

运行状态 waiting_input/queued/leased/running/cancel_requested/succeeded/failed/partial/unknown/canceled；成功需退出码0和匹配当前run/attempt的有效报告（result_required时必需）。side_effects任务未知/强停不自动释放危险资源或自动重跑。一期平台自动整任务重试关闭，人工核对后发起新运行需要新事件且仍受业务去重；不假定服务端幂等。

## 本地运行文件 v1

目录模式扩展：Version.parameter_schema 使用 `x-workbench-input: "directory"` 并声明必填字符串 input_dir、batch_key；版本自动要求 directory-input-v1 能力。目录任务必须固定同项目的目录节点、requires_input=false，business_key_fields 自动包含 batch_key。GET任务列表/详情及创建响应含派生 input_mode(directory/batch/none)，不新增数据库列。运行、事件和定时参数均校验完整绝对路径及批次号，不与 input_batch_id/input_label 混用。

执行电脑本地配置可选 input_roots，未配置时禁止目录输入。只有实际配置根目录的执行器才报告目录能力。目录领取响应增加可选 input_directory，原 input_files 为空；旧模式不增加该字段。运行前检查目标和允许根目录、递归文件、链接及容量，生成原 context.input_manifest：name 为包含子目录的相对名，path 为原文件绝对路径，sha256 为读取摘要；parameters.input_dir 保留原路径。不复制、不上传、不冻结，处理期间上游须保持目录稳定。详情与操作见 [文件夹输入](folder-input-guide.md)。

执行器生成 context.json：protocol_version,run_id,attempt_id,operation,parameters,input_manifest（name/path/sha256数组）,output_dir,state_dir,cancel_file。context 不含平台/节点/钉钉凭据。执行器在 command 后追加 `--context-file <absolute path>`。旧程序通过包内适配入口转换参数。

结果 result.json 位于 output_dir，字段 protocol_version="1",run_id,attempt_id,status(success/failed/partial/unknown/canceled),summary（最多2000字符）,notification_text（可选、非空且最多2000字符，仅供开启成功通知的任务在运行成功时推送，未提供则使用 summary）,metrics（可选object）,artifacts（可选数组{name,required:boolean}）。脚本不接触机器人凭据，也不能自行选择接收群；群由工作台任务通知规则指定。同目录临时文件完成后原子替换。required产物缺失不判成功；可选产物失败只影响归档状态。进度 JSONL 为可选，不作为心跳/成功判据。

开发节点 isolation=development 只执行无副作用测试任务，不能宣称操作系统级隔离。正式节点必须在真实独立任务身份下执行且验证秘密/目录权限；side_effects发布与启用保留业务授权门禁。

当前实现不接受开启 WB_ALLOW_SIDE_EFFECTS：全局资源/业务确认与正式独立身份尚未完成，不是修改环境变量即可解除的生产开关。业务凭据引用/注入与正式 nine 上传仍未开发完成。数据结构仅用于当前全新隔离库；初始化发现既有表缺少当前必需字段会拒绝，不执行隐式迁移。

所有鉴权写入取得数据库事务锁后再核验身份与人员会话，避免慢上传或锁等待期间撤权失效。请求事务在发送成功响应前提交；成功受理可立即查询。真实并发/网络测试见 tests/backend/test_commit_visibility.py 与 tests/review/。

## 设计与验证安排

界面为安静、密集但清晰的中文运维台：浅灰底、白色工作区、深蓝灰侧栏、青绿色操作强调、红/橙异常提示，8px间距，6px圆角，系统中文字体，数字等宽；桌面以表格和详情侧栏为主，小屏允许表格滚动。空状态直接引导真实登记，无模拟任务冒充运行记录。

验证依次覆盖契约/登录权限/登记，实际进程与结果冲突，两类触发与重启去重，通知故障，再做跨阶段回归。外部接口用本地测试服务隔离；数据库并发、实际子进程和核心业务转换不 Mock。实际通过与未执行分开记录。
