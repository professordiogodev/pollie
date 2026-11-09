.PHONY: help install install-dev run-api run-worker run-web test test-integration lint format check env clean

help:
	@echo "Targets:"
	@echo "  make install           Install runtime dependencies"
	@echo "  make install-dev       Install runtime + test + lint dependencies"
	@echo "  make run-api           API with hot reload (http://localhost:8000)"
	@echo "  make run-worker        The vote worker (metrics on :9100)"
	@echo "  make run-web           Dev web server with /api proxy (http://localhost:8080)"
	@echo "  make test              Unit tests with coverage (no services needed)"
	@echo "  make test-integration  Tests against real PostgreSQL and Redis (needs DATABASE_URL and REDIS_URL)"
	@echo "  make lint              ruff check and format check"
	@echo "  make format            ruff format and import sorting"
	@echo "  make check             lint + tests (what CI runs)"
	@echo "  make env               Create .env from .env.example"
	@echo "  make clean             Remove caches"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

run-api:
	uvicorn pollie.api.main:app --reload --port 8000

run-worker:
	python -m pollie.worker

run-web:
	python web/devserver.py

test:
	pytest -m "not integration" --cov

test-integration:
	pytest -m integration -v

lint:
	ruff check pollie tests web
	ruff format --check pollie tests web

format:
	ruff check --fix pollie tests web
	ruff format pollie tests web

check: lint test

env:
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo "created .env")

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
