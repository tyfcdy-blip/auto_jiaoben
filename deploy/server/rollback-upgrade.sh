#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/upgrade-common.sh"
upgrade_lock
snapshot="${1:-}"
[[ -n "${snapshot}" ]] || die "请指定本次升级输出的恢复点绝对路径"
snapshot="$(realpath -e "${snapshot}")"
[[ "${snapshot}" == "${DATA_DIR}/upgrade-backups/"* && -f "${snapshot}/SHA256SUMS" ]] || die "恢复点路径不合法"
(cd "${snapshot}" && sha256sum -c SHA256SUMS)
read -r old_id old_os old_arch old_version old_revision <"${snapshot}/old-image.txt"
[[ "${old_os}" == linux && "${old_arch}" == amd64 ]] || die "恢复点平台不符"
docker load -i "${snapshot}/old-image.tar" >/dev/null
verify_upgrade_image "${old_id}" "${old_id}" "${old_version}" "${old_revision}"
read -r -p '将回滚应用镜像，保留全部数据库与文件，派发保持关闭。输入 ROLLBACK：' confirmation
[[ "${confirmation}" == ROLLBACK ]] || die "已取消"
trap upgrade_failure ERR
pause_and_check
compose stop api worker
# 只恢复配置，不覆盖新秘密、状态或数据库。新增文件任务旧版不理解，禁止直接恢复派发。
tar -C "${APP_DIR}" -xzf "${snapshot}/configuration.tar.gz" .env.production compose.yaml compose.external-db.yaml compose.shared-nginx.yaml
set_env_value "${ENV_FILE}" WB_IMAGE "${old_id}"
set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false
printf '%s\n' '已回滚旧版本；新文件任务不兼容，禁止开启派发，先重新升级或专项迁移。' >"${APP_DIR}/state/rollback-dispatch-blocked"
compose up -d --no-build --no-deps api worker
wait_upgrade_ready
trap - ERR
printf '镜像已回滚；数据库、文件、审计保留，数据库 gate 与环境派发均关闭。禁止使用首次安装回滚脚本。\n'
