FROM python:3.10-slim

WORKDIR /app

# 系统依赖: curl(健康检查) + gcc/sqlite3编译依赖(避免python原生模块装不上)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用Docker缓存层), --retries防网络抖动
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt

# 复制代码
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY tests/ ./tests/
COPY .env.example ./.env.example
COPY requirements.txt ./requirements.txt

# 数据目录可写(兼容Zeabur非root用户运行)
RUN mkdir -p /app/data \
    && chmod -R 777 /app/data /app/static /app/.env.example

ENV PYTHONPATH=/app
ENV DB_DRIVER=sqlite
ENV DB_URL=sqlite:////app/data/erp.db
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# 健康检查: 探/health轻量JSON端点(不触DB/静态/CDN), start-period延长到75s兜底
HEALTHCHECK --interval=15s --timeout=3s --start-period=75s --retries=8 \
  CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

# 启动: seed失败不阻塞uvicorn; 用timeout避免seed卡死; 同时确保uvicorn是exec替换PID1
CMD ["sh", "-c", "timeout 90 python scripts/seed_data.py >/proc/1/fd/1 2>/proc/1/fd/2 || true; exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --timeout-keep-alive 30"]
