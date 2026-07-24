# =============================================================================
# PACCA Makefile — developer convenience commands
#
# Usage:
#   make install      Install package + dev dependencies into current environment
#   make test         Run the fast unit test suite (no API calls, ~10 seconds)
#   make test-cov     Run unit tests with HTML coverage report
#   make test-all     Run everything except clinical (requires API key)
#   make test-clinical Run LLM-as-judge clinical evaluation (requires API key)
#   make lint         Run ruff linter
#   make typecheck    Run mypy type checker
#   make clean        Remove build artifacts and __pycache__
#
# First-time setup:
#   python -m venv venv
#   source venv/bin/activate     (Mac/Linux)
#   venv\Scripts\activate        (Windows)
#   make install
# =============================================================================

.PHONY: install test test-cov test-all test-clinical test-postgres lint typecheck clean help \
        sme-author sme-author-test sme-author-status sme-author-help \
        sme-author-web sme-author-web-build sme-author-web-e2e

# ── Installation ──────────────────────────────────────────────────────────────

install:
	pip install -e ".[dev]"
	@echo "Verifying critical packages..."
	pip install uuid7 pytest-asyncio pytest-cov --quiet
	@echo "Install complete. Run 'make test' to verify."

# ── Data ──────────────────────────────────────────────────────────────────────

seed:
	@echo "Seeding ChromaDB guideline collection (idempotent)..."
	python -m pacca.api.seed_db
	@echo "Seed complete. A fresh store now serves grounded guideline context (B4)."

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	@echo "Running fast unit tests (no API calls)..."
	pytest tests/unit/ tests/clinical/test_clinical_accuracy.py -m "not clinical" -v

test-cov:
	@echo "Running unit tests with coverage report..."
	pytest tests/unit/ -m "not clinical" \
		--cov=pacca \
		--cov-report=html:htmlcov \
		--cov-report=term-missing \
		--cov-fail-under=80
	@echo "Coverage report: htmlcov/index.html"

test-all:
	@echo "Running all fast tests (unit + dataset integrity)..."
	pytest tests/ -m "not clinical" -v

test-clinical:
	@echo "Running full clinical evaluation (requires ANTHROPIC_API_KEY, ~3-5 minutes)..."
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then \
		echo "ERROR: ANTHROPIC_API_KEY is not set. Export it before running clinical tests."; \
		exit 1; \
	fi
	pytest tests/clinical/ -m clinical -v

test-postgres:
	@echo "Real-Postgres integration tests (catches SQLite-masked bugs like B2/B3)..."
	@echo "Starting throwaway Postgres 16 on :5499..."
	@docker rm -f pacca-test-pg >/dev/null 2>&1 || true
	@docker run -d --rm --name pacca-test-pg \
		-e POSTGRES_USER=t -e POSTGRES_PASSWORD=t -e POSTGRES_DB=t \
		-p 5499:5432 postgres:16-alpine >/dev/null
	@until docker exec pacca-test-pg pg_isready -U t >/dev/null 2>&1; do sleep 1; done
	@POSTGRES_TEST_URL="postgresql+asyncpg://t:t@localhost:5499/t" \
		pytest tests/integration -m postgres -v; \
		status=$$?; \
		echo "Tearing down throwaway Postgres..."; \
		docker stop pacca-test-pg >/dev/null 2>&1 || true; \
		exit $$status

# ── Code quality ──────────────────────────────────────────────────────────────

lint:
	ruff check src/ tests/

typecheck:
	mypy src/pacca/

format:
	ruff format src/ tests/

# ── Utilities ─────────────────────────────────────────────────────────────────

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Cleaned."

help:
	@echo "Available commands:"
	@echo "  make install            Install package + dev dependencies"
	@echo "  make test               Fast unit tests (no API calls)"
	@echo "  make test-cov           Unit tests + HTML coverage report"
	@echo "  make test-all           All fast tests"
	@echo "  make test-clinical      Clinical LLM evaluation (needs API key)"
	@echo "  make lint               Ruff linter"
	@echo "  make typecheck          Mypy type checker"
	@echo "  make clean              Remove build artifacts"
	@echo ""
	@echo "SME Case Authoring Agent (CLI):"
	@echo "  make sme-author         Launch the interactive new-case workflow"
	@echo "  make sme-author-status  Print dataset state + milestone gaps"
	@echo "  make sme-author-test    Run unit tests for the SME-authoring module"
	@echo "  make sme-author-help    Print the SME-author CLI help"
	@echo ""
	@echo "SME Case Authoring (Web UI):"
	@echo "  make sme-author-web        Start backend (uvicorn) + frontend (vite) dev servers"
	@echo "  make sme-author-web-build  Build the production frontend bundle into frontend/dist"
	@echo "  make sme-author-web-e2e    Run Playwright smoke tests (installs Chromium if needed)"

# ── SME Case Authoring Agent ──────────────────────────────────────────────────
# Convenience targets for clinicians + engineers using the SME authoring tool.
# See docs/SME_CASE_AGENT_USER_MANUAL.md for the full walkthrough.

sme-author:
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then \
		echo "ERROR: ANTHROPIC_API_KEY is not set. Export it before running."; \
		echo "  export ANTHROPIC_API_KEY=sk-ant-..."; \
		exit 1; \
	fi
	pacca sme-author new

sme-author-status:
	pacca sme-author status

sme-author-test:
	@echo "Running SME-authoring module unit tests..."
	pytest tests/unit/sme_authoring/ -v

sme-author-help:
	pacca sme-author --help

# ── SME Authoring Web UI ──────────────────────────────────────────────────────
# Starts the backend FastAPI server + the frontend Vite dev server in the
# same shell. Browse http://localhost:3000/sme-author to open the surface.
#
# Both servers shut down together when you Ctrl-C — the trap handler kills
# the background uvicorn process when the foreground vite exits.

sme-author-web:
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then \
		echo "ERROR: ANTHROPIC_API_KEY is not set. Export it before running."; \
		echo "  export ANTHROPIC_API_KEY=sk-ant-..."; \
		exit 1; \
	fi
	@if [ ! -d "frontend/node_modules" ]; then \
		echo "Installing frontend dependencies..."; \
		cd frontend && npm install; \
	fi
	@echo "Starting backend on :8000 and frontend on :3000..."
	@echo "Browse: http://localhost:3000/sme-author"
	@trap 'kill 0' EXIT INT TERM; \
		uvicorn pacca.api.main:app --reload --port 8000 & \
		cd frontend && npm run dev

sme-author-web-build:
	@echo "Building production frontend bundle..."
	@if [ ! -d "frontend/node_modules" ]; then \
		echo "Installing frontend dependencies..."; \
		cd frontend && npm install; \
	fi
	cd frontend && npm run build
	@echo "Bundle ready in frontend/dist/"

sme-author-web-e2e:
	@echo "Running Playwright smoke tests (requires browsers installed)..."
	@if [ ! -d "frontend/node_modules/@playwright" ]; then \
		echo "Installing frontend dependencies..."; \
		cd frontend && npm install; \
	fi
	@if [ ! -d "$$HOME/Library/Caches/ms-playwright" ] && [ ! -d "$$HOME/.cache/ms-playwright" ]; then \
		echo "Browsers not installed. Running: cd frontend && npx playwright install --with-deps chromium"; \
		cd frontend && npx playwright install --with-deps chromium; \
	fi
	cd frontend && npm run test:e2e
