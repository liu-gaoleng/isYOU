# 「热读」内容引擎生产镜像
# 多阶段构建：builder 装依赖，runtime 仅含运行所需，体积更小
#
# 构建：    docker build -t redu/content-engine:latest .
# 运行 web：docker run --rm -p 8000:8000 --env-file .env redu/content-engine:latest
# 运行管线：docker run --rm --env-file .env redu/content-engine:latest \
#              python -m content_engine.stages.run_all

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# 系统编译依赖（psycopg / sentence-transformers 编译期需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先拷依赖描述，利用 docker layer cache
COPY pyproject.toml ./
COPY content_engine/__init__.py content_engine/__init__.py

# 只装运行时依赖（dev 依赖留给 CI）
RUN pip install --upgrade pip \
    && pip install --prefix=/install .

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    # sentence-transformers 模型缓存目录（容器内可挂卷持久化）
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers

WORKDIR /app

# 仅装运行时所需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

# 拷贝 builder 产出的 site-packages
COPY --from=builder /install /usr/local

# 拷贝业务代码
COPY content_engine ./content_engine
COPY pyproject.toml ./

# 模型缓存目录（首次启动会下载 bge-small-zh-v1.5 ≈100MB）
RUN mkdir -p "$HF_HOME" "$SENTENCE_TRANSFORMERS_HOME" \
    && chown -R app:app /app

USER app
EXPOSE 8000

# 健康检查（/healthz 由 FastAPI 提供）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# 默认启动 web 服务；worker / oneshot 任务通过覆盖 CMD 使用同一镜像
CMD ["sh", "-c", "uvicorn content_engine.api.app:app --host 0.0.0.0 --port ${PORT} --workers 2"]
