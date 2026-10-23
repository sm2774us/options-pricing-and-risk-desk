.PHONY: setup dev test test-backend test-frontend e2e lint build up down
PY ?= python3

setup:  ## install backend + frontend dependencies
	cd backend && $(PY) -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm ci

dev:  ## single-process backend (:8000) + Vite dev server (:5173)
	cd backend && PYTHONPATH=src .venv/bin/python -m optdesk all & \
	cd frontend && npm run dev

test: test-backend test-frontend  ## unit + integration tests

test-backend:
	cd backend && PYTHONPATH=src .venv/bin/pytest -q

test-frontend:
	cd frontend && npm run typecheck && npm test

e2e:  ## browser end-to-end tests (needs `npx playwright install chromium`)
	cd frontend && npm run test:e2e

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run format:check && npm run typecheck

build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down
