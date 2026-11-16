#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"

require_root

for command in docker curl openssl sha256sum awk grep sed find ss; do
  command -v "${command}" >/dev/null 2>&1 || die "缺少命令：${command}"
done

[[ "$(uname -m)" == "x86_64" ]] || die "服务器必须是 x86_64"
docker info >/dev/null 2>&1 || die "Docker Engine 未运行"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 不可用"
container_running "${DB_CONTAINER}" || die "共享 PostgreSQL 容器未运行"
container_running "${NGINX_CONTAINER}" || die "共享 Nginx 容器未运行"
docker network inspect "${DB_NETWORK}" >/dev/null 2>&1 || die "数据库网络不存在：${DB_NETWORK}"
docker network inspect "${INGRESS_NETWORK}" >/dev/null 2>&1 || die "入口网络不存在：${INGRESS_NETWORK}"

docker inspect -f '{{json .NetworkSettings.Networks}}' "${DB_CONTAINER}" | grep -q "${DB_NETWORK}" \
  || die "PostgreSQL 未加入预期数据库网络"
docker inspect -f '{{json .NetworkSettings.Networks}}' "${NGINX_CONTAINER}" | grep -q "${INGRESS_NETWORK}" \
  || die "Nginx 未加入预期入口网络"

[[ -f "${PAYLOAD_DIR}/automation-workbench_${RELEASE_VERSION}_linux-amd64.tar" ]] || die "缺少离线镜像"
(cd "${DELIVERY_DIR}" && sha256sum -c SHA256SUMS)

data_free_kb="$(df -Pk /data | awk 'NR==2 {print $4}')"
root_free_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
[[ "${data_free_kb}" =~ ^[0-9]+$ && "${data_free_kb}" -ge 83886080 ]] \
  || die "/data 可用空间不足80GiB"
[[ "${root_free_kb}" =~ ^[0-9]+$ && "${root_free_kb}" -ge 2097152 ]] \
  || die "根分区可用空间不足2GiB"

if ss -lnt | awk '{print $4}' | grep -Eq '(^|:)18080$'; then
  existing="$(docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter 'label=com.docker.compose.service=api' --format '{{.Names}}')"
  [[ -n "${existing}" ]] || die "宿主机18080端口已被其他程序占用"
fi

nginx_config="$(resolve_nginx_config)"
docker exec "${NGINX_CONTAINER}" nginx -t >/dev/null
curl --connect-timeout 5 --max-time 10 -fsS "${EXISTING_READY_URL}" >/dev/null \
  || die "原 data-service 健康入口不可用"

printf '预检通过\n'
printf 'Nginx配置：%s\n' "${nginx_config}"
printf '/data可用：%s KiB；根分区可用：%s KiB\n' "${data_free_kb}" "${root_free_kb}"
