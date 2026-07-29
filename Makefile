.PHONY: setup generate dev test reset

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
