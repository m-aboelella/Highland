FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir .

ENV HIGHLAND_SEED_DIR=/app/data/seed
ENV HIGHLAND_RUNTIME_DIR=/data/runtime

ENTRYPOINT ["highland-mocks"]
