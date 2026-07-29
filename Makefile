.PHONY: setup generate dev test reset lint typecheck web-test web-build repository-check ci

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'
	.venv/bin/highland-mocks generate

generate:
	.venv/bin/highland-mocks generate

dev:
	.venv/bin/highland-mocks dev

test:
	.venv/bin/pytest

reset:
	.venv/bin/highland-mocks reset

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy
	cd web && npx tsc --noEmit

web-test:
	cd web && npm test

web-build:
	cd web && npm run build

repository-check:
	.venv/bin/python scripts/check_repository.py

ci: lint typecheck repository-check test web-test web-build
