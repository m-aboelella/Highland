.PHONY: bootstrap setup lock generate dev test reset lint typecheck web-test web-build repository-check ci release-check compose-smoke

bootstrap:
	./bootstrap.sh

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.lock
	.venv/bin/pip install --no-deps -e .
	.venv/bin/highland-mocks generate

lock:
	.venv/bin/pip-compile pyproject.toml --output-file=requirements.lock --strip-extras
	.venv/bin/pip-compile pyproject.toml --extra=dev --output-file=requirements-dev.lock --strip-extras

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
	.venv/bin/python scripts/check_dependency_locks.py

ci: lint typecheck repository-check test web-test web-build

compose-smoke:
	./scripts/compose_smoke.sh

release-check: ci
	.venv/bin/pytest tests/docs tests/unit/test_maintenance.py \
		tests/acceptance/test_meeting_preparation.py \
		tests/acceptance/test_deployment_investigation.py \
		tests/acceptance/test_weekly_customer_health.py
	./scripts/compose_smoke.sh
