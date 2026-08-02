FROM python:3.13-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-dejavu-core \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
COPY data ./data
COPY config ./config
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

ENV HIGHLAND_SEED_DIR=/app/data/seed
ENV HIGHLAND_RUNTIME_DIR=/data/runtime
ENV HIGHLAND_MODEL_PRICE_CONFIG=/app/config/model_prices.json
ENV HIGHLAND_TOOL_POLICY_CONFIG=/app/config/tool_policy.json
ENV HIGHLAND_AGENT_PROFILE_CONFIG=/app/config/agents/general.json
ENV HIGHLAND_RETRIEVAL_RELEVANCE_CONFIG=/app/config/evaluation/retrieval-relevance.json
ENV HIGHLAND_RETRIEVAL_BASELINE_CONFIG=/app/config/evaluation/retrieval-baseline.json

CMD ["highland", "app"]
