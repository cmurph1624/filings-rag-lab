# The serving app (`frl serve`). Built per commit; the deploy runs it as linux/arm64 (t4g).
#
# The image keeps the repo layout at /app (src, config, configs, corpus, migrations) with the
# project installed editable, so the paths the code resolves from its own location
# (config/prices.toml, corpus/companies.csv, migrations/) work unchanged.
#
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t frl:$(git rev-parse --short HEAD) .

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin frl
WORKDIR /app
COPY --from=build /app /app
COPY config ./config
COPY configs ./configs
COPY corpus ./corpus
COPY migrations ./migrations
ARG GIT_SHA=unknown
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 GIT_SHA=$GIT_SHA
USER frl
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/version', timeout=4)"]
# compose.prod.yaml sets --forwarded-allow-ips to the Caddy container's fixed address.
CMD ["frl", "serve", "--config", "configs/serve.toml", "--host", "0.0.0.0", "--port", "8000"]
