#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

require_root
cat <<'EOF'
该操作仅回退首次上线入口：关闭派发、停止工作台容器、恢复部署前Nginx配置。
不会删除工作台数据库、账号、秘密、平台卷、镜像或备份；保留现场用于核对。
EOF
read -r -p '输入 ROLLBACK：' confirmation
[[ "${confirmation}" == "ROLLBACK" ]] || die "用户取消回退"

if [[ -f "${ENV_FILE}" ]]; then
  set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false
  compose stop worker api || true
fi

nginx_config="$(resolve_nginx_config)"
nginx_backup="${nginx_config}.automation-workbench.bak"
if [[ -f "${nginx_backup}" ]]; then
  [[ -f "${APP_DIR}/state/nginx_config_path" && -f "${APP_DIR}/state/nginx_installed_sha256" ]] \
    || die "缺少Nginx安装状态，拒绝覆盖共享配置"
  recorded_config="$(<"${APP_DIR}/state/nginx_config_path")"
  expected_digest="$(<"${APP_DIR}/state/nginx_installed_sha256")"
  current_digest="$(sha256sum "${nginx_config}" | awk '{print $1}')"
  [[ "${recorded_config}" == "${nginx_config}" ]] || die "Nginx配置路径与安装记录不一致"
  [[ "${expected_digest}" =~ ^[a-f0-9]{64}$ && "${current_digest}" == "${expected_digest}" ]] \
    || die "Nginx配置在部署后已发生变化，拒绝自动覆盖；请人工合并受管location"
  rollback_snapshot="${nginx_config}.automation-workbench.rollback-current.$$"
  cp -p "${nginx_config}" "${rollback_snapshot}"
  cp -p "${nginx_backup}" "${nginx_config}"
  if ! docker exec "${NGINX_CONTAINER}" nginx -t; then
    cp -p "${rollback_snapshot}" "${nginx_config}"
    docker exec "${NGINX_CONTAINER}" nginx -t >/dev/null || true
    rm -f "${rollback_snapshot}"
    die "Nginx回退配置检查失败，已恢复回退前配置"
  fi
  docker exec "${NGINX_CONTAINER}" nginx -s reload
  rm -f "${rollback_snapshot}"
fi
curl --connect-timeout 5 --max-time 10 -fsS "${EXISTING_READY_URL}" >/dev/null \
  || die "回退后原data-service入口异常"
printf '首次上线入口已回退；数据库和文件均保留，后续清理必须另行审批\n'
