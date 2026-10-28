.PHONY: dev test lint precommit clean ui api

dev:
	docker compose up

api:
	python -m uvicorn app.main:app --reload

ui:
	python -m streamlit run ui/app.py

test:
	python -m pytest tests/ -v

lint:
	python -m ruff check app/ tests/ ui/
	python -m mypy app/

precommit:
	pre-commit run --all-files

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache
