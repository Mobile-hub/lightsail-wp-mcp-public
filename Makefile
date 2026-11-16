# lightsail-wp-mcp — run `make` to list targets. Logic lives in bin/, this is the menu.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help setup serve test lint fmt lock clean

help: ## Show this help
	@awk 'BEGIN{FS=":.*## "} /^[a-z-]+:.*## /{printf "  \033[36m%-10s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

setup: ## Create .venv (locked) and a local .env with generated secrets
	@bin/setup

serve: ## Run the server on loopback with the local .env
	@bin/wpmcp serve

test: ## Lint + tests with coverage (+ shellcheck if installed)
	@bin/test

lint: ## Ruff lint only
	@.venv/bin/ruff check src tests

fmt: ## Format Python code
	@.venv/bin/ruff format src tests && .venv/bin/ruff check --fix src tests

lock: ## Pin dependencies with hashes (uv.lock + deploy/requirements.txt)
	@bin/lock

clean: ## Remove local caches (keeps .env and var/)
	@rm -rf .venv .pytest_cache .ruff_cache .coverage build dist src/*.egg-info
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
