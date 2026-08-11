FROM python:3.10-slim

WORKDIR /app

# 系统依赖: 只保留最小集, 不装curl(改用内置python探针), 避免apt-get网络失败导致构建
RUN apt-get update 2>/dev/null \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc libffi-dev \
    2>/dev/null; \
    rm -rf /var/lib/apt/lists/*

# 先装依赖层(利用Docker缓存), 网络重试+长超时
COPY requirements.txt .
RUN pip install --no-cache-dir \
        --default-timeout=300 \
        --retries 10 \
        --trusted-host pypi.org \
        --trusted-host pypi.python.org \
        --trusted-host files.pythonhosted.org \
        -r requirements.txt

# 复制代码
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY tests/ ./tests/
COPY .env.example ./.env.example
COPY requirements.txt ./requirements.txt

# 数据目录权限
RUN mkdir -p /app/data && chmod -R 777 /app/data /app/static /app/.env.example 2>/dev/null || true

ENV PYTHONPATH=/app
ENV DB_DRIVER=sqlite
ENV DB_URL=sqlite:////app/data/erp.db
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

# 健康检查: 纯Python urllib探针(不依赖curl/wget安装), 只要Python在就一定能探测
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=10 \
  CMD python -c "import urllib.request,sys; \
    try: \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1) \
    except Exception: \
      sys.exit(1)"

# 启动: 极简CMD, 不依赖timeout, 不做/proc重定向, seed失败不阻塞, 用exec替换PID1给uvicorn
CMD ["sh", "-c", "python scripts/seed_data.py; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
