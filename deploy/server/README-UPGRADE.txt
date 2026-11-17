已有工作台升级说明（v0.6.0-rc.4 → v0.6.1-rc.1）

本包使用Docker部署，服务器仍为 https://ai.muyaju.net/automation/。
已经安装过的服务器只执行 upgrade.sh；不要重跑 install.sh 或 rollback-first-install.sh。
账号密码、电脑绑定、任务、环境配置、秘密、输入/结果文件保留；不改共享Nginx，不重建数据库账号。

一、上传和校验
把tar.gz和同名.sha256上传到 /data/automation-workbench-delivery，不要把离线镜像放在空间不足的根分区。
cd /data/automation-workbench-delivery
sha256sum -c automation-workbench-server-v0.6.1-rc.1.tar.gz.sha256
tar -xzf automation-workbench-server-v0.6.1-rc.1.tar.gz
cd automation-workbench-server-v0.6.1-rc.1
bash scripts/upgrade.sh
按提示输入 UPGRADE。如果提示在途/未知状态，先在工作台核对，禁止强制终止或绕过。

二、升级会做什么
1. 校验离线包与linux/amd64镜像身份，锁定升级，核对当前数据库。
2. 同时关闭数据库和环境派发开关，确认无在途业务，再停平台API/Worker。
3. 在 /data/automation-workbench/upgrade-backups/ 本次目录备份工作台单库、平台文件卷、原配置和旧镜像。
   备份含凭据，请限制访问，勿发群聊或提交Git；磁盘应容纳数据库+文件卷+旧镜像备份及新镜像。
4. 只切换本应用镜像，显式执行追加四张表及索引的迁移，启动并检查健康。
5. 成功后仍保持任务派发关闭。不要将“排队中”理解为电脑已开始执行。

三、更新执行电脑
服务器升级后，在工作台执行电脑页下载新版接入包并解压。
已绑定电脑双击“升级已绑定电脑.cmd”，核对地址并输入 YES；无需重新注册或发放令牌。
安装完毕、确认无运行中或未知任务后，注销当前Windows用户再登录。
旧代码目录保留，原凭据、运行时、目录及恢复状态保留。工作台应显示在线且支持新文件能力。
新电脑仍用“安装并连接.cmd”。执行器依赖Python3.12；脚本自身依赖另按程序说明安装。
执行电脑主动通过HTTPS连接服务器，不要求同一局域网，不需要服务器挂载Windows文件夹。

四、确认配置并启用测试
管理员先给电脑配置输入读取目录、输出写入目录及项目授权，等待同步。
进入电脑→在这台电脑上创建任务→选择程序→选择Excel→填写固定日期→选择结果目录。
定时计划可选择计划当天；普通字符串账期可选上一月。Excel内容始终由脚本读取。
确认已有排队任务/定时计划确实允许执行；启用派发会放行全部符合条件的任务。
在服务器本交付目录执行：
bash scripts/enable-dispatch.sh
按提示输入 ENABLE，然后手动运行合成测试任务，检查：程序成功、平台归档完成、本机另存成功。
输入/结果保留45天，沿用既有配置。本次不自动发送钉钉消息；通知通道需单独确认。

五、回滚
只在没有运行中、未知进程和待保存结果时，使用升级输出的恢复点：
bash scripts/rollback-upgrade.sh /data/automation-workbench/upgrade-backups/本次目录
按提示输入 ROLLBACK。恢复原镜像与配置，数据库和结果不回灌、不删表。
回滚后派发被双重关闭并增加阻断标记，因为旧版不理解新文件任务；不要手动删除标记开启派发。
重新升级成功后才能按正常验收步骤启用。备份恢复为单独灾难恢复操作，不属于此镜像回滚命令。

六、验证边界
本地自动化测试和隔离Docker/PostgreSQL演练不等于生产实测。
正式执行后仍需核对公网入口、原data-service、实际执行电脑、真实业务和钉钉通知。
失败保留日志和恢复点；不要删除卷、清理镜像或重复首次安装来“修复”。
