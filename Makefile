.PHONY: install dev-install test lint typecheck build clean run-daily run-offline

install:
	pip install --break-system-packages -e .

dev-install:
	pip install --break-system-packages -e ".[dev]"

test:
	pytest tests/ -v

lint:
	ruff check src/ scripts/ tests/

format:
	ruff format src/ scripts/ tests/

typecheck:
	mypy src/nse_scanner

build:
	python -m build --wheel --sdist

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info .mypy_cache .ruff_cache .pytest_cache

run-daily:
	python scripts/run_daily.py

run-offline:
	python scripts/run_daily.py --offline-fixture

ci: lint typecheck test build
