#!/bin/sh

set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$repository_dir"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop or Docker Engine, then run this again." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required (the 'docker compose' command)." >&2
  exit 1
fi

cohere_key=${COHERE_API_KEY:-}
if [ -z "$cohere_key" ]; then
  if [ ! -t 0 ]; then
    echo "Set COHERE_API_KEY or run ./bootstrap.sh in a terminal to enter it securely." >&2
    exit 1
  fi
  printf "Cohere API key: "
  previous_tty=$(stty -g)
  trap 'stty "$previous_tty"' EXIT HUP INT TERM
  stty -echo
  IFS= read -r cohere_key
  stty "$previous_tty"
  trap - EXIT HUP INT TERM
  printf "\n"
fi

if [ -z "$cohere_key" ]; then
  echo "A Cohere API key is required for the live learning stack." >&2
  exit 1
fi

case "$cohere_key" in
  *[!A-Za-z0-9._-]*)
    echo "The API key contains unsupported characters." >&2
    exit 1
    ;;
esac

environment_file="$repository_dir/.env"
temporary_file=$(mktemp "${TMPDIR:-/tmp}/highland-env.XXXXXX")
trap 'rm -f "$temporary_file"' EXIT HUP INT TERM

if [ -f "$environment_file" ]; then
  awk '
    !/^COHERE_API_KEY=/ &&
    !/^HIGHLAND_MODEL_BACKEND=/
  ' "$environment_file" > "$temporary_file"
fi
printf "HIGHLAND_MODEL_BACKEND=cohere\n" >> "$temporary_file"
printf "COHERE_API_KEY=%s\n" "$cohere_key" >> "$temporary_file"
chmod 600 "$temporary_file"
mv "$temporary_file" "$environment_file"
trap - EXIT HUP INT TERM

echo "Starting Highland and synchronizing the fictional enterprise data..."
docker compose up --build --detach --wait

echo
echo "Highland is ready:"
echo "  Workspace:  http://localhost:3000"
echo "  API docs:   http://localhost:8080/docs"
echo "  Mock APIs:  http://localhost:8099"
echo
echo "Follow the code: docs/LEARNING_PATH.md"
echo "Stop (keep data): docker compose down"
echo "Reset demo data: docker compose run --rm highland-api highland reset --yes"
