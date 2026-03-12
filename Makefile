SHELL := /bin/bash

.DEFAULT_GOAL := help

ifneq (,$(wildcard .env))
ENV_FILE ?= .env
else
ENV_FILE ?= .env.example
endif

COMPOSE ?= docker compose
COMPOSE_CMD := $(COMPOSE) --env-file $(ENV_FILE)
SERVICE ?=
BACKUP_ROOT ?= backups
BACKUP_STAMP ?= $(shell date +%Y%m%d-%H%M%S)

.PHONY: help up down logs restart build healthcheck migrate backup lint test

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "; printf "\nEchoThink ClawCluster Make targets\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2} END {print ""}' $(MAKEFILE_LIST)

up: ## Build and start the stack in detached mode
	@echo "Using env file: $(ENV_FILE)"
	@$(COMPOSE_CMD) up -d --build

down: ## Stop the stack and remove orphaned containers
	@$(COMPOSE_CMD) down --remove-orphans

logs: ## Tail logs; set SERVICE=<service> to scope output
	@if [[ -n "$(SERVICE)" ]]; then \
		echo "Tailing logs for $(SERVICE)"; \
		$(COMPOSE_CMD) logs -f --tail=200 $(SERVICE); \
	else \
		echo "Tailing logs for all services"; \
		$(COMPOSE_CMD) logs -f --tail=200; \
	fi

restart: ## Restart all services or one service with SERVICE=<service>
	@if [[ -n "$(SERVICE)" ]]; then \
		echo "Restarting $(SERVICE)"; \
		$(COMPOSE_CMD) restart $(SERVICE); \
	else \
		echo "Restarting all services"; \
		$(COMPOSE_CMD) restart; \
	fi

build: ## Build all locally-built images or one service with SERVICE=<service>
	@if [[ -n "$(SERVICE)" ]]; then \
		echo "Building $(SERVICE)"; \
		$(COMPOSE_CMD) build $(SERVICE); \
	else \
		echo "Building all locally-built services"; \
		$(COMPOSE_CMD) build; \
	fi

healthcheck: ## Verify container state and public health endpoints
	@set -euo pipefail; \
	echo "==> Container status"; \
	$(COMPOSE_CMD) ps; \
	echo "==> Nginx edge"; \
	curl -fsS http://127.0.0.1/healthz >/dev/null; \
	echo "==> Matrix versions"; \
	curl -fsS http://127.0.0.1/_matrix/client/versions >/dev/null; \
	echo "==> HiClaw manager"; \
	curl -fsS http://127.0.0.1:$${HICLAW_MANAGER_PORT:-8088}/health >/dev/null; \
	echo "==> Intake bridge"; \
	curl -fsS http://127.0.0.1/api/intake/health >/dev/null; \
	echo "==> Policy bridge"; \
	curl -fsS http://127.0.0.1/api/policy/health >/dev/null; \
	echo "==> Publisher bridge"; \
	curl -fsS http://127.0.0.1/api/publisher/health >/dev/null; \
	echo "==> Observability bridge"; \
	curl -fsS http://127.0.0.1/api/observability/health >/dev/null; \
	echo "All health checks passed."

migrate: ## Run migrations for services that expose /app/scripts/migrate.sh or alembic
	@set -euo pipefail; \
	for service in hiclaw-manager intake-bridge publisher-bridge policy-bridge observability-bridge; do \
		if $(COMPOSE_CMD) ps --services --filter status=running | grep -qx "$$service"; then \
			echo "Running migrations for $$service"; \
			$(COMPOSE_CMD) exec -T $$service sh -lc 'if [[ -x /app/scripts/migrate.sh ]]; then /app/scripts/migrate.sh; elif command -v alembic >/dev/null 2>&1; then alembic upgrade head; else echo "No migration entrypoint defined for $(hostname)"; fi'; \
		else \
			echo "Skipping $$service (not running)"; \
		fi; \
	done

backup: ## Create a timestamped backup from mounted service volumes
	@set -euo pipefail; \
	backup_dir="$(BACKUP_ROOT)/$(BACKUP_STAMP)"; \
	mkdir -p "$$backup_dir"; \
	cp docker-compose.yml "$$backup_dir/"; \
	if [[ -f "$(ENV_FILE)" ]]; then cp "$(ENV_FILE)" "$$backup_dir/"; fi; \
	echo "Backing up Higress state"; \
	$(COMPOSE_CMD) exec -T higress sh -lc 'tar czf - -C /var/lib/higress .' > "$$backup_dir/higress-data.tgz"; \
	echo "Backing up Tuwunel state"; \
	$(COMPOSE_CMD) exec -T tuwunel sh -lc 'tar czf - -C /data .' > "$$backup_dir/tuwunel-data.tgz"; \
	echo "Backing up HiClaw manager state"; \
	$(COMPOSE_CMD) exec -T hiclaw-manager sh -lc 'tar czf - -C /var/lib/clawcluster .' > "$$backup_dir/hiclaw-manager-data.tgz"; \
	echo "Backing up bridge state"; \
	for service in intake-bridge publisher-bridge policy-bridge observability-bridge; do \
		if $(COMPOSE_CMD) ps --services --filter status=running | grep -qx "$$service"; then \
			$(COMPOSE_CMD) exec -T $$service sh -lc 'tar czf - -C /var/lib/clawcluster .' > "$$backup_dir/$$service-data.tgz"; \
		fi; \
	done; \
	echo "Backup created at $$backup_dir"

lint: ## Validate compose syntax and lint Markdown/YAML when local tools exist
	@set -euo pipefail; \
	echo "Validating docker-compose rendering"; \
	$(COMPOSE) --env-file $(ENV_FILE) config >/dev/null; \
	if command -v yamllint >/dev/null 2>&1; then \
		yamllint docker-compose.yml; \
	else \
		echo "yamllint not installed; skipped"; \
	fi; \
	if command -v markdownlint >/dev/null 2>&1; then \
		markdownlint README.md CLAUDE.md; \
	else \
		echo "markdownlint not installed; skipped"; \
	fi

test: ## Run static repository verification checks
	@set -euo pipefail; \
	echo "Checking required foundation files"; \
	test -f CLAUDE.md; \
	test -f README.md; \
	test -f Makefile; \
	test -f .env.example; \
	test -f docker-compose.yml; \
	echo "Rendering compose with example environment"; \
	$(COMPOSE) --env-file .env.example config >/dev/null; \
	echo "Static verification passed."
