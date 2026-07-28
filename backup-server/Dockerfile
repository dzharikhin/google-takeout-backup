FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:$PATH:/app:/app/utils"
ARG PYTHONUNBUFFERED=1
ENV PYTHONUNBUFFERED=$PYTHONUNBUFFERED
RUN uv sync --frozen --no-install-project --no-default-groups --group backup
RUN mkdir -p /app/utils && curl -fSL -o /app/utils/gpth https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux && chmod +x /app/utils/gpth
COPY auth.py cookies.py backup-server/backup.py ./
COPY keys_EN.csv keys_RU.csv ./
ENTRYPOINT ["python"]
CMD ["backup.py"]
