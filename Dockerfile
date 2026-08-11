FROM python:3.10-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖(利用缓存)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY app/ ./app/
COPY scripts/ ./scripts/

# 数据目录
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV PYTHONPATH=/app
ENV DB_DRIVER=sqlite
ENV DB_URL=sqlite:///./data/erp.db
EXPOSE 8000

# 启动时自动建表+seed(幂等)
CMD ["sh", "-c", "python scripts/seed_data.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
