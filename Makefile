# ==============================================================================
# Airutil Management Makefile
#
# Commands for managing the multi-container OS lab vault platform
# ==============================================================================

.PHONY: up down logs reset ps stats build test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

reset:
	docker compose down -v

ps:
	docker compose ps

stats:
	docker stats --no-stream

build:
	docker compose build

test:
	docker compose exec -T app1 pytest -o cache_dir=/tmp/.pytest_cache tests/
