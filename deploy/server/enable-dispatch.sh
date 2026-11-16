#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

require_root
[[ -f "${ENV_FILE}" ]] || die "尚未安装生产配置"
"${DELIVERY_DIR}/scripts/verify.sh"
read -r -p '确认已完成备份、浏览器、执行电脑、钉钉及业务试点验收后，输入 ENABLE：' confirmation
[[ "${confirmation}" == "ENABLE" ]] || die "用户取消启用派发"

rollback_dispatch() {
  local failure_code=$?
  trap - ERR
  set +e
  if set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false \
      && grep -Fxq 'WB_DISPATCH_ENABLED=false' "${ENV_FILE}"; then
    compose stop worker >/dev/null 2>&1
    if compose up -d --no-build --force-recreate api worker >/dev/null 2>&1; then
      printf '启用失败，已把任务派发恢复为关闭状态。\n' >&2
    else
      compose stop worker >/dev/null 2>&1
      printf '启用失败且服务恢复未完成；Worker已停止，请人工检查。\n' >&2
    fi
  else
    compose stop worker >/dev/null 2>&1
    printf '启用失败且无法确认派发配置已关闭；已尝试停止Worker，禁止继续运行并请人工检查。\n' >&2
  fi
  exit "${failure_code}"
}

trap rollback_dispatch ERR
set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED true
compose config --quiet
compose up -d --no-build --force-recreate api worker
"${DELIVERY_DIR}/scripts/verify.sh"
trap - ERR
printf '任务派发已启用\n'
