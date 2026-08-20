# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/packages/eval_common

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 evaluator \
    && useradd --uid 10001 --gid evaluator --create-home evaluator

WORKDIR /app

COPY packages/eval_common /app/packages/eval_common
COPY services/worker/requirements.txt /tmp/requirements.txt
COPY services/worker/requirements-eval-libs.txt /tmp/requirements-eval-libs.txt

ARG INSTALL_EVAL_LIBS=true
RUN pip install --upgrade pip \
    && pip install /app/packages/eval_common -r /tmp/requirements.txt \
    && if [ "$INSTALL_EVAL_LIBS" = "true" ]; then pip install -r /tmp/requirements-eval-libs.txt; fi

COPY services/worker /app

USER evaluator
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import bedrock_judge, test_runner"
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-u", "/app/test_runner.py"]
