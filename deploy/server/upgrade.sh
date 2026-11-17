#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/upgrade-common.sh"
upgrade_lock
(cd "${DELIVERY_DIR}" && sha256sum -c SHA256SUMS)
docker load -i "${PAYLOAD_DIR}/automation-workbench_${RELEASE_VERSION}_linux-amd64.tar" >/dev/null
verify_upgrade_image "${RELEASE_IMAGE}" "${RELEASE_IMAGE_CONFIG_DIGEST}" "${RELEASE_VERSION}" "${RELEASE_COMMIT}"
old_container="$(compose ps -q api)"
old_image="$(docker inspect -f '{{.Image}}' "${old_container}")"
[[ "${old_image}" != "${RELEASE_IMAGE_CONFIG_DIGEST}" ]] || die "已经是目标版本，拒绝重复升级"
volume="$(docker inspect "${old_container}" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"
[[ -n "${volume}" ]] || die "平台数据必须是已确认的命名卷"
check_backup_space "${volume}" "${old_image}"
read -r -p '将暂停派发、备份本平台并升级；不改账号/Nginx。输入 UPGRADE：' confirmation
[[ "${confirmation}" == UPGRADE ]] || die "已取消"
umask 077
snapshot="${DATA_DIR}/upgrade-backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "${snapshot}"
# 先保存原配置（含原派发值），失败时也有据可查。
tar -C "${APP_DIR}" -czf "${snapshot}/configuration.tar.gz" .env.production compose.yaml compose.external-db.yaml compose.shared-nginx.yaml secrets state
docker image inspect "${old_image}" --format '{{.Id}} {{.Os}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}' >"${snapshot}/old-image.txt"
printf '%s\n' "${volume}" >"${snapshot}/volume.txt"
trap upgrade_failure ERR
pause_and_check
compose stop api worker
# 停止所有平台写入后形成一致的单库与文件恢复点，不导出共享实例其他库。
docker exec -u postgres "${DB_CONTAINER}" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d automation_workbench -Fc' >"${snapshot}/database.dump"
docker exec -i -u postgres "${DB_CONTAINER}" pg_restore --list <"${snapshot}/database.dump" >"${snapshot}/database.contents"
docker run --rm --network none --user 0:0 --read-only --entrypoint tar \
  --mount "type=volume,src=${volume},dst=/platform,readonly" \
  --mount "type=bind,src=${snapshot},dst=/backup" "${old_image}" \
  -C /platform -czf /backup/platform.tar.gz .
docker image save "${old_image}" -o "${snapshot}/old-image.tar"
(cd "${snapshot}" && sha256sum configuration.tar.gz database.dump database.contents platform.tar.gz old-image.txt old-image.tar volume.txt >SHA256SUMS)
printf '%s\n' "${snapshot}" >"${APP_DIR}/state/latest-upgrade-backup"
set_env_value "${ENV_FILE}" WB_IMAGE "${RELEASE_IMAGE}"
set_env_value "${ENV_FILE}" WB_RELEASE_VERSION "${RELEASE_VERSION}"
set_env_value "${ENV_FILE}" WB_VCS_REF "${RELEASE_COMMIT}"
compose run --rm --no-deps --entrypoint python api -m workbench.node_files_migration apply
compose run --rm --no-deps --entrypoint python api -m workbench.node_files_migration check
compose up -d --no-build --no-deps api worker
wait_upgrade_ready
# 只有新版本迁移与健康探针均成功，才解除旧版回滚留下的禁止派发标记。
if [[ -f "${APP_DIR}/state/rollback-dispatch-blocked" ]]; then
  mv "${APP_DIR}/state/rollback-dispatch-blocked" "${snapshot}/previous-rollback-dispatch-blocked"
fi
trap - ERR
printf '升级完成；账号、任务和电脑绑定保留，派发仍关闭。恢复点：%s\n' "${snapshot}"
printf '先升级执行电脑并验收，再按升级说明同时恢复数据库和环境派发开关。\n'
