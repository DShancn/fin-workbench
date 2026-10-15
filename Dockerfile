# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Debian slim 镜像默认不带 tzdata，而本项目所有时间语义都依赖
# zoneinfo("Asia/Shanghai")。缺它会在容器里静默退化，进而让
# 「先后不可判定」的证据被误判——所以必须显式装。
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依赖单独一层，改代码不至于重装 akshare
RUN pip install --no-cache-dir akshare duckdb pandas plotly requests streamlit

COPY pyproject.toml ./
COPY src ./src
COPY app ./app
COPY rules ./rules

# akshare 取的是境内数据源，容器里若继承了宿主代理会随机 SSL 截断，
# 代码内已做绕过，这里再兜一层
ENV NO_PROXY="eastmoney.com,cninfo.com.cn,sina.com.cn,szse.cn,sse.com.cn,10jqka.com.cn,dfcfw.com"

EXPOSE 8501
CMD ["streamlit", "run", "app/main.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
