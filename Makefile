.PHONY: install test lint eval web docker

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff format --check .
	ruff check .

eval:
	python scripts/run_eval.py --baseline evals/baseline/baseline_results.json --fail-on-regression

web:
	uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000

docker:
	docker compose up --build
