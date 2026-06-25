ARG PLAYWRIGHT_VERSION=1.60.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble
ARG PLAYWRIGHT_VERSION=1.60.0
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:$PATH:/app:/app/utils"
RUN mkdir -p /app/utils
RUN uv sync --frozen --no-install-project --no-default-groups --group backup
RUN curl -fSL -o /app/utils/gpth https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux && chmod +x /app/utils/gpth
COPY auth.py backup-server/backup.py ./
COPY keys_EN.csv keys_RU.csv ./
ENTRYPOINT ["python"]
CMD ["backup.py"]
