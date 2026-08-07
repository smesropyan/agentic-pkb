.PHONY: install test lint fmt types layers check

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

types:
	uv run mypy

layers:
	uv run lint-imports

check: lint types layers test
