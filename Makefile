.PHONY: dev backend frontend migrate test lint install

dev: backend frontend

backend:
	cd backend && .venv\Scripts\activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

install:
	cd backend && python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"
	cd frontend && npm install

migrate:
	cd backend && .venv\Scripts\alembic upgrade head

test:
	cd backend && .venv\Scripts\pytest tests/ -v

lint:
	cd backend && .venv\Scripts\ruff check app/
