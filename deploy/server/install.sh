#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

require_root
"${DELIVERY_DIR}/scripts/preflight.sh"

cat <<'EOF'
即将执行以下生产变更：
1. 导入固定的 linux/amd64 Docker 镜像；
2. 首次安装时创建独立数据库、低权限账号和精确的 pg_hba 拒绝规则；
3. 初始化工作台表和管理员，启动 API/Worker，但保持任务派发关闭；
4. 备份并修改 ai.muyaju.net 所在 Nginx 配置，检查后平滑重载；
5. 不删除任何现有卷、数据库、账号、镜像或其他项目容器。
EOF
read -r -p '确认维护窗口、备份责任人和变更授权后，输入 DEPLOY：' confirmation
[[ "${confirmation}" == "DEPLOY" ]] || die "用户取消部署"

install -d -m 0700 "${APP_DIR}" "${APP_DIR}/secrets" "${APP_DIR}/state"
install -d -m 0755 "${DATA_DIR}" "${DATA_DIR}/releases"

archive="${PAYLOAD_DIR}/automation-workbench_${RELEASE_VERSION}_linux-amd64.tar"
docker load --input "${archive}" >/dev/null
image_config_digest="$(docker image inspect "${RELEASE_IMAGE}" -f '{{.Id}}')"
image_arch="$(docker image inspect "${RELEASE_IMAGE}" -f '{{.Architecture}}')"
image_version="$(docker image inspect "${RELEASE_IMAGE}" -f '{{index .Config.Labels "org.opencontainers.image.version"}}')"
image_revision="$(docker image inspect "${RELEASE_IMAGE}" -f '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
[[ "${image_config_digest}" == "${RELEASE_IMAGE_CONFIG_DIGEST}" && "${image_arch}" == "amd64" \
   && "${image_version}" == "${RELEASE_VERSION}" && "${image_revision}" == "${RELEASE_COMMIT}" ]] \
  || die "镜像config摘要、架构或OCI标签与交付清单不一致"
install -m 0644 "${archive}" "${DATA_DIR}/releases/$(basename "${archive}")"

for name in compose.yaml compose.external-db.yaml compose.shared-nginx.yaml; do
  install -m 0644 "${PAYLOAD_DIR}/${name}" "${APP_DIR}/${name}"
done
if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0600 "${PAYLOAD_DIR}/.env.production" "${ENV_FILE}"
fi
set_env_value "${ENV_FILE}" WB_IMAGE "${RELEASE_IMAGE}"
set_env_value "${ENV_FILE}" WB_RELEASE_VERSION "${RELEASE_VERSION}"
set_env_value "${ENV_FILE}" WB_VCS_REF "${RELEASE_COMMIT}"
set_env_value "${ENV_FILE}" WB_DISPATCH_ENABLED false

secret_names=(database_password database_url encryption_key monitor_token admin_password)
existing_secrets=0
for name in "${secret_names[@]}"; do
  [[ -f "${APP_DIR}/secrets/${name}" ]] && existing_secrets=$((existing_secrets + 1))
done
[[ "${existing_secrets}" -eq 0 || "${existing_secrets}" -eq "${#secret_names[@]}" ]] \
  || die "秘密目录不完整，拒绝覆盖或猜测恢复"

db_admin_user="$(docker exec -u postgres "${DB_CONTAINER}" sh -c 'printf "%s" "${POSTGRES_USER:-postgres}"')"
protected_db="$(docker exec -u postgres "${DB_CONTAINER}" sh -c 'printf "%s" "${POSTGRES_DB:-postgres}"')"
[[ "${db_admin_user}" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || die "共享数据库管理员名格式异常"
[[ "${protected_db}" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || die "现有生产数据库名格式异常"
[[ "${protected_db}" != "automation_workbench" ]] || die "现有生产库与工作台目标库重名"

if [[ "${existing_secrets}" -eq 0 ]]; then
  role_exists="$(docker exec -u postgres "${DB_CONTAINER}" psql -X -At -U "${db_admin_user}" -d postgres \
    -c "SELECT 1 FROM pg_roles WHERE rolname='automation_workbench_user'")"
  db_exists="$(docker exec -u postgres "${DB_CONTAINER}" psql -X -At -U "${db_admin_user}" -d postgres \
    -c "SELECT 1 FROM pg_database WHERE datname='automation_workbench'")"
  [[ -z "${role_exists}" && -z "${db_exists}" ]] \
    || die "工作台数据库或账号已存在但本机没有完整秘密，需人工恢复原凭据"
  app_password="$(openssl rand -hex 32)"
  printf "CREATE ROLE automation_workbench_user LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 25;\n" \
    "${app_password}" | docker exec -i -u postgres "${DB_CONTAINER}" \
      psql -X -v ON_ERROR_STOP=1 -U "${db_admin_user}" -d postgres >/dev/null
  if ! docker exec -u postgres "${DB_CONTAINER}" createdb -U "${db_admin_user}" \
      -O automation_workbench_user automation_workbench; then
    docker exec -u postgres "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_admin_user}" \
      -d postgres -c 'DROP ROLE automation_workbench_user' >/dev/null || true
    die "创建工作台数据库失败；已尝试撤销本次新角色"
  fi

  umask 077
  printf '%s\n' "${app_password}" > "${APP_DIR}/secrets/database_password"
  printf 'postgresql+psycopg://automation_workbench_user:%s@db:5432/automation_workbench\n' \
    "${app_password}" > "${APP_DIR}/secrets/database_url"
  openssl rand -base64 32 | tr '/+' '_-' | tr -d '\n' > "${APP_DIR}/secrets/encryption_key"
  printf '\n' >> "${APP_DIR}/secrets/encryption_key"
  openssl rand -base64 48 | tr '/+' '_-' | tr -d '\n' > "${APP_DIR}/secrets/monitor_token"
  printf '\n' >> "${APP_DIR}/secrets/monitor_token"
  openssl rand -base64 32 | tr '/+' '_-' | tr -d '\n' > "${APP_DIR}/secrets/admin_password"
  printf '\n' >> "${APP_DIR}/secrets/admin_password"
  chmod 0444 "${APP_DIR}/secrets/"*
  unset app_password
fi

hba_file="$(docker exec -u postgres "${DB_CONTAINER}" psql -X -At -U "${db_admin_user}" -d postgres -c 'SHOW hba_file')"
[[ "${hba_file}" == /* ]] || die "无法取得PostgreSQL pg_hba.conf路径"
hba_marker="# automation-workbench-managed-reject"
hba_rule="host ${protected_db} automation_workbench_user all reject ${hba_marker}"
existing_hba_rule="$(docker exec -u postgres "${DB_CONTAINER}" grep -F "${hba_marker}" "${hba_file}" || true)"
if [[ -n "${existing_hba_rule}" && "${existing_hba_rule}" != "${hba_rule}" ]]; then
  die "发现不一致的工作台pg_hba规则，拒绝自动覆盖"
fi
if [[ -z "${existing_hba_rule}" ]]; then
  hba_backup="$(docker exec -u postgres "${DB_CONTAINER}" sh -c '
    set -eu
    file="$1"; rule="$2"; backup="${file}.automation-workbench.$(date +%Y%m%d%H%M%S).bak"
    cp -p "$file" "$backup"
    temporary="${file}.automation-workbench.tmp"
    { printf "%s\n" "$rule"; cat "$file"; } > "$temporary"
    chmod --reference="$file" "$temporary"
    mv -f "$temporary" "$file"
    printf "%s" "$backup"
  ' sh "${hba_file}" "${hba_rule}")"
  printf '%s\n' "${hba_backup}" > "${APP_DIR}/state/pg_hba_backup_path"
  docker exec -u postgres "${DB_CONTAINER}" psql -X -v ON_ERROR_STOP=1 -U "${db_admin_user}" \
    -d postgres -c 'SELECT pg_reload_conf()' >/dev/null
fi

docker run --rm --network "${DB_NETWORK}" \
  --mount "type=bind,src=${APP_DIR}/secrets/database_password,dst=/run/db_password,readonly" \
  -e WB_PROTECTED_DATABASE="${protected_db}" "${RELEASE_IMAGE}" python -c '
import os, pathlib, psycopg, sys
p = pathlib.Path("/run/db_password").read_text().strip()
psycopg.connect(host="db", port=5432, dbname="automation_workbench",
                user="automation_workbench_user", password=p, connect_timeout=5).close()
try:
    psycopg.connect(host="db", port=5432, dbname=os.environ["WB_PROTECTED_DATABASE"],
                    user="automation_workbench_user", password=p, connect_timeout=5).close()
except psycopg.OperationalError:
    raise SystemExit(0)
raise SystemExit(2)
' || die "数据库权限隔离验证失败：工作台账号必须只能连接自己的库"

compose config --quiet
compose run --rm --no-deps init-db
compose up -d --no-build api worker

admin_exists=0
if compose run --rm --no-deps --entrypoint python api -c '
from sqlalchemy import select
from workbench.config import Settings
from workbench.db import database
from workbench.models import User
engine, factory = database(Settings())
try:
    with factory() as db:
        raise SystemExit(0 if db.scalar(select(User.id).where(User.is_admin.is_(True))) else 1)
finally:
    engine.dispose()
'; then
  admin_exists=1
fi
if [[ "${admin_exists}" -eq 0 ]]; then
  compose --profile admin run --rm --no-deps init-admin
fi

ready=0
for _ in $(seq 1 60); do
  if curl --connect-timeout 2 --max-time 3 -fsS \
      'http://127.0.0.1:18080/automation/api/v1/health/ready' >/dev/null; then
    ready=1
    break
  fi
  sleep 2
done
[[ "${ready}" -eq 1 ]] || die "工作台本机健康检查未就绪"

nginx_config="$(resolve_nginx_config)"
nginx_basename="$(basename "${nginx_config}")"
nginx_backup="${nginx_config}.automation-workbench.bak"
nginx_digest_file="${APP_DIR}/state/nginx_installed_sha256"
nginx_path_file="${APP_DIR}/state/nginx_config_path"
nginx_state_files=0
[[ -f "${nginx_digest_file}" ]] && nginx_state_files=$((nginx_state_files + 1))
[[ -f "${nginx_path_file}" ]] && nginx_state_files=$((nginx_state_files + 1))
[[ "${nginx_state_files}" -eq 0 || "${nginx_state_files}" -eq 2 ]] \
  || die "Nginx安装状态不完整，拒绝继续"
if grep -Fq '# automation-workbench-managed:start' "${nginx_config}"; then
  [[ "${nginx_state_files}" -eq 2 ]] \
    || die "Nginx已有工作台片段但缺少可信安装状态，拒绝覆盖或接管"
  recorded_nginx_config="$(<"${nginx_path_file}")"
  recorded_nginx_digest="$(<"${nginx_digest_file}")"
  current_nginx_digest="$(sha256sum "${nginx_config}" | awk '{print $1}')"
  [[ "${recorded_nginx_config}" == "${nginx_config}" \
     && "${recorded_nginx_digest}" =~ ^[a-f0-9]{64}$ \
     && "${current_nginx_digest}" == "${recorded_nginx_digest}" ]] \
    || die "共享Nginx配置在首次安装后已变化，拒绝重写回退基线"
elif [[ "${nginx_state_files}" -ne 0 || -e "${nginx_backup}" ]]; then
  die "Nginx片段、备份与安装状态不一致，需人工核对后再继续"
fi
docker run --rm --user 0:0 \
  --mount "type=bind,src=${NGINX_CONF_DIR},dst=/host" \
  --mount "type=bind,src=${DELIVERY_DIR},dst=/delivery,readonly" \
  "${RELEASE_IMAGE}" python /delivery/scripts/patch_nginx.py \
    --file "/host/${nginx_basename}" \
    --snippet /delivery/scripts/nginx-location.conf \
    --backup "/host/$(basename "${nginx_backup}")" >/dev/null
if ! docker exec "${NGINX_CONTAINER}" nginx -t; then
  [[ -f "${nginx_backup}" ]] && cp -p "${nginx_backup}" "${nginx_config}"
  docker exec "${NGINX_CONTAINER}" nginx -t >/dev/null || true
  die "Nginx检查失败，已尝试恢复部署前备份"
fi
docker exec "${NGINX_CONTAINER}" nginx -s reload
if [[ "${nginx_state_files}" -eq 0 ]]; then
  sha256sum "${nginx_config}" | awk '{print $1}' > "${nginx_digest_file}"
  printf '%s\n' "${nginx_config}" > "${nginx_path_file}"
  chmod 0600 "${nginx_digest_file}" "${nginx_path_file}"
fi

printf '%s\n' "${RELEASE_VERSION} ${RELEASE_COMMIT}" > "${APP_DIR}/state/installed_release"
"${DELIVERY_DIR}/scripts/verify.sh"

cat <<EOF
部署完成，任务派发仍保持关闭。
管理地址：https://ai.muyaju.net/automation/
首次管理员账号：admin
首次密码仅在服务器文件中：${APP_DIR}/secrets/admin_password
当前版本仅允许 side_effects=false 的无外部写入脚本。
完成浏览器、执行电脑、钉钉和备份验收后，再执行：bash ${DELIVERY_DIR}/scripts/enable-dispatch.sh
EOF
