.PHONY: install generate run benchmark simulate serve test e2e lint format clean docker-build docker-run docker-up docker-down

install:
	python -m pip install -e ".[dev]"

generate:
	python -m settlegraph.cli generate --total-records 1000 --anomaly-rate 0.15 --seed 42

run:
	python -m settlegraph.cli run

benchmark:
	python -m settlegraph.cli benchmark

simulate:
	python -m settlegraph.cli simulate

serve:
	python -m settlegraph.cli serve --port 8080

test:
	pytest -v --basetemp .pytest-tmp

e2e:
	python scripts/run_e2e.py

lint:
	ruff check src tests scripts datagen api

format:
	ruff format src tests scripts datagen api

format-check:
	ruff format --check src tests scripts datagen api

docker-build:
	docker build -t settlegraph:latest .

docker-run:
	docker run -p 8080:8080 --name settlegraph-app settlegraph:latest

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').glob('.pytest*')]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').glob('.ruff*')]"

