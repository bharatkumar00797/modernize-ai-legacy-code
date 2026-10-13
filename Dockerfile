# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- build stage
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels .

# -------------------------------------------------------------- runtime stage
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="Modernize AI" \
      org.opencontainers.image.description="AI legacy code modernization agent: rule-detected smells, LLM-ranked allowlisted refactors, sandboxed and gated by your tests" \
      org.opencontainers.image.source="https://github.com/bharatkumar00797/modernize-ai-legacy-code" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    MODERNIZE_PROVIDER=mock

RUN groupadd --system --gid 10001 modernize \
 && useradd --system --uid 10001 --gid modernize --home-dir /app --shell /usr/sbin/nologin modernize

COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links=/wheels modernize-ai-legacy-code \
 && rm -rf /wheels

WORKDIR /app
USER modernize
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000'), timeout=4)"]

CMD ["sh", "-c", "exec modernize serve --host 0.0.0.0 --port \"${PORT:-8000}\""]
