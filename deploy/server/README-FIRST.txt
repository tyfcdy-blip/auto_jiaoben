自动化工作台 v0.6.0-rc.4｜FinalShell Docker 部署包
====================================================

适用服务器：CentOS 7 / x86_64、Docker 26.1.4、Compose 2.27.1。
固定入口：https://ai.muyaju.net/automation/

上传建议
--------

把整个 tar.gz 上传到 `/data/automation-workbench-delivery`，不要放到根分区空间较小的
`/mnt/docker`。必须把同名 `.sha256` 文件一起上传，先校验外层包，再执行：

    cd /data/automation-workbench-delivery
    sha256sum -c automation-workbench-server-v0.6.0-rc.4.tar.gz.sha256
    tar -xzf automation-workbench-server-v0.6.0-rc.4.tar.gz
    cd automation-workbench-server-v0.6.0-rc.4
    bash scripts/preflight.sh
    bash scripts/install.sh

如果预检提示无法唯一确定 Nginx 配置，先指定实际 HTTPS server 配置再重试：

    export WB_NGINX_CONFIG_FILE=/mnt/docker/basic/nginx/conf.d/实际文件.conf

部署脚本的边界
--------------

1. 首次安装会创建独立数据库 `automation_workbench` 和低权限账号
   `automation_workbench_user`，连接上限25。
2. 脚本会先备份 pg_hba.conf，再增加一条只针对该账号访问现有生产数据库的拒绝规则；
   不修改其他账号规则。
3. 脚本会备份目标 Nginx 配置，插入 `/automation/` location，先 `nginx -t`，通过后才重载。
4. API 和 Worker 使用 Docker 部署，默认保持 `WB_DISPATCH_ENABLED=false`。部署成功不代表任务已正式派发。
5. 不执行 `docker system prune`、`docker compose down -v`，不删除任何现有容器、卷、数据库或备份。
6. 当前发布候选只允许 `side_effects=false` 的无外部写入脚本；正式接口上传等外部写入能力尚未放行。

部署后
------

查看初始管理员密码（不要粘贴到群聊或部署日志）：

    cat /mnt/docker/automation-workbench/secrets/admin_password

重复验收：

    bash scripts/verify.sh

完成浏览器、执行电脑、钉钉测试群、备份恢复和无外部写入业务试点后，才执行：

    bash scripts/enable-dispatch.sh

首次上线需撤回入口时：

    bash scripts/rollback-first-install.sh

回退脚本只关闭派发、停止工作台容器并恢复部署前 Nginx 配置；数据库、账号、秘密、平台卷和
镜像全部保留用于核对。需要删除数据库或恢复数据时必须另行审批，不能在本脚本中顺便执行。
