.PHONY: up down fixed logs load load-light smoke ps clean

# Bring up the stack in the BUGGY configuration (the default).
up:
	FIX_ENABLED=false docker compose up --build -d
	@echo "app:        http://localhost:8000"
	@echo "grafana:    http://localhost:3000  (dashboard: Widget Service — Reliability)"
	@echo "prometheus: http://localhost:9090"

# Bring up the stack with the FIX applied.
fixed:
	FIX_ENABLED=true docker compose up --build -d
	@echo "Running with FIX_ENABLED=true"

down:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f app

smoke:
	@curl -s http://localhost:8000/healthz; echo
	@curl -s http://localhost:8000/readyz; echo
	@curl -s http://localhost:8000/widgets/1; echo

# Heavy load — reproduces the failure when running buggy.
load:
	python3 loadtest/load.py --url http://localhost:8000 --concurrency 30 --duration 20

# Light load — service looks perfectly healthy even when buggy.
load-light:
	python3 loadtest/load.py --url http://localhost:8000 --concurrency 2 --duration 15

clean: down
