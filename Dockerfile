FROM harbor.cminl.oa:8443/uv/instantclient:python3.14@sha256:484bf4b4f9b4247edd06710a2e325cdf9431b83a028df5c41bea3516d29ced57
ARG RELEASE_ID=0.1.0
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="auto-plan" org.opencontainers.image.version=$RELEASE_ID org.opencontainers.image.revision=$SOURCE_REVISION
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 AUTO_PLAN_FRONTEND_DIST=/app/frontend/dist AUTO_PLAN_ORACLE_CLIENT_PATH=/opt/oracle
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
COPY src ./src
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --python /usr/local/bin/python && mkdir -p /app/runtime && printf '{"release":"%s","source_revision":"%s","schema_min":5,"schema_max":5}\n' "$RELEASE_ID" "$SOURCE_REVISION" > /app/runtime/release.json
COPY frontend/dist ./frontend/dist
COPY frontend/package-lock.json ./runtime/frontend-package-lock.json
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s CMD ["/app/.venv/bin/python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000'+os.environ.get('AUTO_PLAN_BASE_PATH','/auto_plan')+'/api/ready',timeout=2)"]
ENTRYPOINT ["/app/.venv/bin/python", "-m", "auto_plan.serve"]
