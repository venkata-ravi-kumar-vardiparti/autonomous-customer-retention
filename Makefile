.PHONY: install test lint typecheck schemas

install:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy --strict src/churnguard/contracts src/churnguard/data src/churnguard/policy

schemas:
	uv run python scripts/export_schemas.py
