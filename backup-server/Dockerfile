FROM mcr.microsoft.com/playwright/python:v1.61.0-noble
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-default --group backup
COPY --chmod=+x https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux /app/utils/gpth
COPY auth.py backup.py ./
COPY keys_EN.csv keys_RU.csv ./
ENV PATH="$PATH:/app:/app/utils"
CMD ["python", "backup.py"]
