#!/usr/bin/env bash
# 只操作工作台单库；共享 PostgreSQL、Nginx 与其他应用保持原状。
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

upgrade_sql() {
  docker exec -i -u postgres "${DB_CONTAINER}" sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d automation_workbench' "$@"
}

upgrade_lock() {
  require_root
  local command
  for command in docker flock tar sha256sum realpath; do
    command -v "${command}" >/dev/null || die "缺少升级命令：${command}"
  done
  [[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] || die "缺少已有安装配置"
  install -d -m 0700 "${APP_DIR}/state"
  exec 9>"${APP_DIR}/state/upgrade.lock"
  flock -n 9 || die "已有升级或回滚正在执行"
  compose config --quiet
  container_running "${DB_CONTAINER}" || die "数据库容器未运行"
  # 从实际 API 连接验证库名，避免把暂停或备份应用在错误数据库。
  compose run --rm --no-deps -T --entrypoint python api -c '
from sqlalchemy import text
from workbench.config import Settings
from workbench.db import database
engine, _ = database(Settings())
with engine.connect() as connection:
    assert connection.scalar(text("select current_database()")) == "automation_workbench"
engine.dispose()
' </dev/null || die "当前应用数据库身份无法确认"
}

pause_and_check() {
  # 单独提交关闭动作：后续在途检查失败也不能把派发恢复成开启。
  upgrade_sql <<'SQL'
BEGIN;
SELECT id FROM public.system_gate FOR UPDATE;
DO $$ BEGIN
  IF (SELECT count(*) FROM public.system_gate) <> 1 OR NOT EXISTS
    (SELECT 1 FROM public.system_gate WHERE id=1 AND schema_version=1) THEN
    RAISE EXCEPTION 'unsupported system gate';
  END IF;
END $$;
UPDATE public.system_gate SET dispatch_enabled=false WHERE id=1;
COMMIT;
SQL
  set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false
  upgrade_sql <<'SQL'
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM public.runs WHERE state NOT IN
      ('queued','waiting_input','succeeded','failed','partial','canceled')) THEN
    RAISE EXCEPTION 'active or unknown runs: wait for completion/reconciliation';
  END IF;
  IF to_regclass('public.run_file_options') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM public.run_file_options f JOIN public.runs r ON r.id=f.run_id
      WHERE f.retry_requested OR (f.save_state='pending' AND r.state NOT IN ('queued','waiting_input'))) THEN
      RAISE EXCEPTION 'file save pending: wait for completion';
    END IF;
  END IF;
END $$;
SQL
}

verify_upgrade_image() {
  local image="$1" expected="$2" version="$3" revision="$4"
  local actual
  actual="$(docker image inspect "${image}" --format '{{.Id}} {{.Os}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}')"
  [[ "${actual}" == "${expected} linux amd64 ${version} ${revision}" ]] || die "镜像身份与恢复点/发布清单不符"
}

require_backup_space() {
  local available="$1" database_bytes="$2" file_bytes="$3" image_bytes="$4" configuration_bytes="$5" value required
  for value in "$available" "$database_bytes" "$file_bytes" "$image_bytes" "$configuration_bytes"; do
    [[ "$value" =~ ^[0-9]{1,15}$ ]] || die "无法可靠取得备份容量，已阻断升级"
  done
  # 不依赖压缩率；增加20%估算余量，再保留至少1GiB，避免挤满共享数据库所在磁盘。
  required=$(( (database_bytes + file_bytes + image_bytes + configuration_bytes) * 12 / 10 + 1073741824 ))
  (( available >= required )) || die "备份磁盘空间不足：需要至少${required}字节，可用${available}字节；尚未暂停派发"
  printf '备份空间检查通过：估算需要%s字节，可用%s字节\n' "$required" "$available"
}

check_backup_space() {
  local volume="$1" image="$2" database_bytes file_bytes image_bytes configuration_bytes available
  install -d -m 0700 "${DATA_DIR}/upgrade-backups"
  database_bytes="$(docker exec -u postgres "${DB_CONTAINER}" sh -c 'exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d automation_workbench -c "SELECT pg_database_size(current_database())"')"
  file_bytes="$(docker run --rm --network none --user 0:0 --read-only --entrypoint du \
    --mount "type=volume,src=${volume},dst=/platform,readonly" "${image}" -sb /platform | awk '{print $1}')"
  image_bytes="$(docker image inspect "${image}" --format '{{.Size}}')"
  configuration_bytes="$(du -sb "${APP_DIR}" | awk '{print $1}')"
  available="$(df -PB1 "${DATA_DIR}/upgrade-backups" | awk 'NR==2 {print $4}')"
  require_backup_space "$available" "$database_bytes" "$file_bytes" "$image_bytes" "$configuration_bytes"
}

upgrade_failure() {
  local code=$?
  trap - ERR
  set +e
  set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false
  if upgrade_sql <<'SQL' >/dev/null 2>&1
DO $$ BEGIN
  IF (SELECT count(*) FROM public.system_gate) <> 1 OR NOT EXISTS
    (SELECT 1 FROM public.system_gate WHERE id=1 AND schema_version=1) THEN
    RAISE EXCEPTION 'unsupported system gate';
  END IF;
  UPDATE public.system_gate SET dispatch_enabled=false WHERE id=1;
END $$;
SQL
  then
    printf '升级/回滚未完成，数据库派发已关闭；不要重跑首次安装。恢复点：%s\n' "${snapshot:-未生成}" >&2
  elif compose stop api worker; then
    printf '无法确认数据库派发关闭，API与Worker已停止。恢复点：%s\n' "${snapshot:-未生成}" >&2
  else
    printf '无法确认派发关闭，且API/Worker停止失败；必须立即人工隔离工作台入口与执行电脑。恢复点：%s\n' "${snapshot:-未生成}" >&2
  fi
  exit "${code}"
}

wait_upgrade_ready() {
  local attempt
  for attempt in $(seq 1 30); do
    if bash "${DELIVERY_DIR}/scripts/verify.sh" >"${APP_DIR}/state/upgrade-verify.log" 2>&1; then
      cat "${APP_DIR}/state/upgrade-verify.log"
      return
    fi
    sleep 2
  done
  die "健康检查失败；详见 state/upgrade-verify.log，派发仍关闭"
}
