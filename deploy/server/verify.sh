#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

require_root
[[ -f "${ENV_FILE}" ]] || die "尚未安装生产配置"

compose ps
curl --connect-timeout 5 --max-time 10 -fsS \
  'http://127.0.0.1:18080/automation/api/v1/health/ready' >/dev/null \
  || die "本机ready检查失败"

header_file="$(mktemp /tmp/automation-workbench-monitor.XXXXXX)"
trap 'rm -f "${header_file}"' EXIT
chmod 0600 "${header_file}"
printf 'header = "Authorization: Bearer %s"\n' \
  "$(tr -d '\r\n' < "${APP_DIR}/secrets/monitor_token")" > "${header_file}"
components="$(curl --config "${header_file}" --connect-timeout 5 --max-time 10 -fsS \
  'http://127.0.0.1:18080/automation/api/v1/health/components')" \
  || die "后台循环或存储健康检查失败"
printf '%s' "${components}" | grep -q '"status":"healthy"' || die "组件状态不是healthy"

curl --connect-timeout 5 --max-time 10 -fsS "${PUBLIC_READY_URL}" >/dev/null \
  || die "公网工作台入口检查失败"
curl --connect-timeout 5 --max-time 10 -fsS "${EXISTING_READY_URL}" >/dev/null \
  || die "原data-service入口回归失败"

printf '验收探针通过：API、Worker、存储、工作台公网入口和原data-service均正常\n'
