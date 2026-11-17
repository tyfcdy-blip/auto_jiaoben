#!/usr/bin/env bash

set -Eeuo pipefail

readonly RELEASE_VERSION="0.6.0-rc.4"
readonly RELEASE_COMMIT="825e310fd567023542140471e31a147bd0f45b1d"
readonly RELEASE_IMAGE="automation-workbench:0.6.0-rc.4"
readonly RELEASE_IMAGE_CONFIG_DIGEST="REPLACE_WITH_IMAGE_CONFIG_DIGEST"
readonly COMPOSE_PROJECT="automation-workbench-production"
readonly APP_DIR="/mnt/docker/automation-workbench"
readonly DATA_DIR="/data/automation-workbench"
readonly DB_CONTAINER="muyaju-data-reservoir-db-1"
readonly DB_NETWORK="muyaju-data-reservoir_database"
readonly INGRESS_NETWORK="basic_basic_net"
readonly NGINX_CONTAINER="nginx"
readonly NGINX_CONF_DIR="/mnt/docker/basic/nginx/conf.d"
readonly PUBLIC_READY_URL="https://ai.muyaju.net/automation/api/v1/health/ready"
readonly EXISTING_READY_URL="https://ai.muyaju.net/data-service/health/ready"

DELIVERY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PAYLOAD_DIR="${DELIVERY_DIR}/payload"
ENV_FILE="${APP_DIR}/.env.production"

die() {
  printf '错误：%s\n' "$*" >&2
  exit 1
}
require_root() {
  [[ "${EUID}" -eq 0 ]] || die "请使用 root 执行"
}

compose() {
  docker compose --project-name "${COMPOSE_PROJECT}" --env-file "${ENV_FILE}" \
    -f "${APP_DIR}/compose.yaml" \
    -f "${APP_DIR}/compose.external-db.yaml" \
    -f "${APP_DIR}/compose.shared-nginx.yaml" "$@"
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

resolve_nginx_config() {
  local configured="${WB_NGINX_CONFIG_FILE:-}"
  if [[ -n "${configured}" ]]; then
    [[ "${configured}" == "${NGINX_CONF_DIR}/"*.conf ]] || die "WB_NGINX_CONFIG_FILE 必须位于 ${NGINX_CONF_DIR}"
    [[ -f "${configured}" && ! -L "${configured}" ]] || die "指定的 Nginx 配置不是普通文件"
    grep -Eq 'server_name[[:space:]][^;]*ai\.muyaju\.net' "${configured}" || die "指定配置不包含 ai.muyaju.net"
    printf '%s\n' "${configured}"
    return
  fi
  mapfile -t candidates < <(grep -El 'server_name[[:space:]][^;]*ai\.muyaju\.net' "${NGINX_CONF_DIR}"/*.conf 2>/dev/null || true)
  [[ "${#candidates[@]}" -eq 1 ]] || die "无法唯一确定 ai.muyaju.net 的 Nginx 配置；请先 export WB_NGINX_CONFIG_FILE=完整路径"
  printf '%s\n' "${candidates[0]}"
}

set_env_value() {
  local file="$1" key="$2" value="$3" temporary
  temporary="${file}.tmp.$$"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { found=0 }
    index($0, key "=")==1 { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "${file}" > "${temporary}"
  chmod --reference="${file}" "${temporary}"
  mv -f "${temporary}" "${file}"
}
