FROM python:3.13-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-dejavu-core \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir .

ENV HIGHLAND_SEED_DIR=/app/data/seed
ENV HIGHLAND_RUNTIME_DIR=/data/runtime

ENTRYPOINT ["highland-mocks"]
