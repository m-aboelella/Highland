#!/bin/sh

set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_dir"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to run the Compose smoke test." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required to run the Compose smoke test." >&2
  exit 1
fi

cleanup() {
  smoke_status=$?
  trap - EXIT HUP INT TERM

  if [ "$smoke_status" -ne 0 ]; then
    echo "Compose smoke test failed; preserving service diagnostics before cleanup." >&2
    docker compose ps --all >&2 || :
    docker compose logs --no-color highland-bootstrap highland-api >&2 || :
  fi

  docker compose down || cleanup_status=$?
  if [ "$smoke_status" -ne 0 ]; then
    exit "$smoke_status"
  fi
  exit "${cleanup_status:-0}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Starting a deterministic Highland stack..."
HIGHLAND_MODEL_BACKEND=scripted COHERE_API_KEY= \
  docker compose up --build --detach --wait

bootstrap_status=$(docker compose ps --all --format json highland-bootstrap)
case "$bootstrap_status" in
  *'"ExitCode":0'*) ;;
  *)
    echo "The index bootstrap did not complete successfully: $bootstrap_status" >&2
    exit 1
    ;;
esac

echo "Checking API health, indexed search, and the web application..."
docker compose exec -T highland-api python - <<'PY'
import json
import urllib.request


def get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read()


status, payload = get("http://127.0.0.1:8080/health")
health = json.loads(payload)
assert status == 200
assert health == {"status": "ok", "model_backend": "scripted"}

request = urllib.request.Request(
    "http://127.0.0.1:8080/discover/search",
    data=json.dumps(
        {
            "query": "Northwind retrieval latency",
            "filters": {"customer_id": "cus_northwind"},
        }
    ).encode(),
    headers={"content-type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=20) as response:
    search = json.load(response)
assert search["results"], "the synchronized index returned no search results"

status, _ = get("http://web:3000")
assert status == 200
PY

echo "Enforcing the committed deterministic retrieval baseline..."
docker compose exec -T highland-api highland eval retrieval --enforce-baseline

echo "Compose smoke test passed; stopping the stack."
