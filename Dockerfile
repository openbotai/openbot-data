FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY openbot_data ./openbot_data
RUN pip install --no-cache-dir ".[service]"

USER nobody
EXPOSE 8080
CMD ["sh", "-c", "uvicorn openbot_data.service:app --host 0.0.0.0 --port ${PORT}"]
