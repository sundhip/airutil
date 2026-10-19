# ==============================================================================
# Airutil Management Makefile
#
# Commands for managing the multi-container OS lab vault platform
# ==============================================================================

.PHONY: up down restart logs reset ps stats build test showcase showcase-auto record backup restore load-test verify-os audit

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart
	docker restart airutil_nginx

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

test-cov:
	docker compose exec -e COVERAGE_FILE=/tmp/.coverage -T app1 pytest -o cache_dir=/tmp/.pytest_cache --cov=app --cov-report=term-missing tests/

showcase:
	bash scripts/airutil-showcase.sh

showcase-auto:
	bash scripts/airutil-showcase.sh --auto

record:
	command -v asciinema >/dev/null 2>&1 && asciinema rec docs/showcase-demo.cast -c "bash scripts/airutil-showcase.sh --auto" || echo "Please install asciinema to record the demo."

backup:
	bash scripts/backup.sh

restore:
	@test -n "$(FILE)" || (echo "Usage: make restore FILE=backups/airutil_backup_YYYYMMDD_HHMMSS.tar.gz" && exit 1)
	bash scripts/restore.sh $(FILE)

load-test:
	bash scripts/load_test.sh

verify-os:
	bash scripts/verify_os.sh

audit:
	docker compose exec -T app1 pip-audit --cache-dir /tmp/.pip-audit-cache
