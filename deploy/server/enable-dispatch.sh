#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/upgrade-common.sh"

require_root
[[ -f "${ENV_FILE}" ]] || die "尚未安装生产配置"
[[ ! -e "${APP_DIR}/state/rollback-dispatch-blocked" ]] || die "已回滚旧版，存在不兼容文件任务；请先重新升级，禁止直接启用派发"
upgrade_lock
compose run --rm --no-deps --entrypoint python api -m workbench.node_files_migration check
"${DELIVERY_DIR}/scripts/verify.sh"
read -r -p '确认已完成备份、浏览器、执行电脑、钉钉及业务试点验收后，输入 ENABLE：' confirmation
[[ "${confirmation}" == "ENABLE" ]] || die "用户取消启用派发"

rollback_dispatch() {
  local failure_code=$?
  trap - ERR
  set +e
  if upgrade_sql <<<'UPDATE public.system_gate SET dispatch_enabled=false WHERE id=1;' >/dev/null 2>&1 \
      && set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false \
      && grep -Fxq 'WB_DISPATCH_ENABLED=false' "${ENV_FILE}"; then
    compose stop api worker >/dev/null 2>&1
    if compose up -d --no-build --force-recreate api worker >/dev/null 2>&1; then
      printf '启用失败，已把任务派发恢复为关闭状态。\n' >&2
    else
      if compose stop api worker >/dev/null 2>&1; then
        printf '启用失败且服务恢复未完成；API与Worker已停止，请人工检查。\n' >&2
      else
        printf '启用失败且无法确认API/Worker已停止；禁止继续，请立即人工检查。\n' >&2
      fi
    fi
  else
    if compose stop api worker >/dev/null 2>&1; then
      printf '启用失败且无法确认派发配置已关闭；API与Worker已停止，请人工检查。\n' >&2
    else
      printf '启用失败且无法确认派发关闭或API/Worker停止；禁止继续，请立即人工检查。\n' >&2
    fi
  fi
  exit "${failure_code}"
}

trap rollback_dispatch ERR
pause_and_check
upgrade_sql <<'SQL'
BEGIN;
SELECT id FROM public.system_gate FOR UPDATE;
DO $$ BEGIN
  IF (SELECT count(*) FROM public.system_gate) <> 1 OR NOT EXISTS
    (SELECT 1 FROM public.system_gate WHERE id=1 AND schema_version=1) THEN
    RAISE EXCEPTION 'unsupported system gate';
  END IF;
END $$;
UPDATE public.system_gate SET dispatch_enabled=true WHERE id=1;
COMMIT;
SQL
set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED true
compose config --quiet
compose up -d --no-build --force-recreate api worker
"${DELIVERY_DIR}/scripts/verify.sh"
trap - ERR
printf '任务派发已启用\n'
