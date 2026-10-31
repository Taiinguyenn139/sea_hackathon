FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir uv==0.8.23
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
COPY harness/ harness/
COPY bank_tools/ bank_tools/
COPY eval/results/ eval/results/
COPY --from=frontend /build/web/dist/ web/dist/
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HARNESS_DB=/data/harness.sqlite3 COOKIE_SECURE=true APP_ENV=production
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1
CMD ["uvicorn", "harness.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
