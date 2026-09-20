.PHONY: install test lint typecheck schemas ab-experiment

install:
	uv sync --extra dev --extra experiments

test:
	uv run pytest

lint:
	uv run ruff check src tests scripts ui experiments

typecheck:
	uv run mypy --strict src/churnguard/contracts src/churnguard/data src/churnguard/policy src/churnguard/telemetry src/churnguard/orchestration src/churnguard/agents src/churnguard/tools src/churnguard/guardrails src/churnguard/offers src/churnguard/approval src/churnguard/execution src/churnguard/api ui experiments

ab-experiment:
	uv run python -m experiments.run_ab --n 30
	uv run python -m experiments.report

schemas:
	uv run python scripts/export_schemas.py
