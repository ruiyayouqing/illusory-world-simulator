# [v1.4 P1-8] 太虚幻境 容器镜像
# 普通玩家仍可用 启动.bat 直接运行；此文件用于容器化部署/服务器托管
# 用法：
#   docker build -t taixuhuanjing:latest .
#   docker compose up -d
# 详见 docker-compose.yml

FROM python:3.11-slim

# 设置时区与编码
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    AUTO_OPEN_BROWSER=0

# 系统依赖：chromadb 需要 build-essential；sqlite3 用于调试
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        sqlite3 \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制应用代码（受 .dockerignore 控制）
COPY app /app

# 容器内运行时挂载点：saves/data/config.json 由卷提供
VOLUME ["/app/saves", "/app/data", "/app/config.json"]

EXPOSE 8004

# 容器内必须监听 0.0.0.0；通过环境变量覆盖 config.json 的 host 设置
# AUTO_OPEN_BROWSER=0 禁止容器内打开浏览器
ENV HOST=0.0.0.0 PORT=8004

CMD ["python", "server.py"]
