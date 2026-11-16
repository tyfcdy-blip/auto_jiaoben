# 开发默认使用受支持系列；正式构建须通过 ARG 传入经过审核的镜像摘要。
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG RELEASE_VERSION=dev
ARG VCS_REF=unknown
FROM ${NODE_IMAGE} AS frontend-build
WORKDIR /frontend
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime
ARG RELEASE_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="automation-workbench" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 workbench \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin workbench \
    && mkdir -p /app/data && chown 10001:10001 /app/data
COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 agent/ ./agent/
COPY --chown=10001:10001 contracts/ ./contracts/
COPY --chown=10001:10001 deploy/windows-agent/ ./deploy/windows-agent/
COPY --from=frontend-build --chown=10001:10001 /frontend/dist/ ./frontend/dist/
COPY --chown=10001:10001 deploy/healthcheck.py ./deploy/healthcheck.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["uvicorn", "workbench.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
