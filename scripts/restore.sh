#!/usr/bin/env bash
# ==============================================================================
# Airutil Disaster Recovery: Automated Restore Script
#
# Usage:
#   ./scripts/restore.sh backups/<timestamp>
# ==============================================================================
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_directory>"
    echo "Example: $0 backups/20261019_120000"
    exit 1
fi

RESTORE_SRC="$1"

if [ ! -d "${RESTORE_SRC}" ]; then
    echo "ERROR: Directory ${RESTORE_SRC} does not exist!" >&2
    exit 1
fi

echo "======================================================================"
echo "AIRUTIL DISASTER RECOVERY: RESTORE PROCEDURE"
echo "Source: ${RESTORE_SRC}"
echo "======================================================================"

# 1. Verify Checksums
if [ -f "${RESTORE_SRC}/SHA256SUMS" ]; then
    echo "==> [1/3] Verifying SHA-256 Checksums..."
    (
        cd "${RESTORE_SRC}"
        if command -v sha256sum > /dev/null 2>&1; then
            sha256sum -c SHA256SUMS
        else
            shasum -a 256 -c SHA256SUMS
        fi
    )
    echo "    ✓ Checksum verification passed."
fi

# 2. Restore PostgreSQL Database
echo "==> [2/3] Restoring PostgreSQL Relational Database..."
if [ -f "${RESTORE_SRC}/postgres_backup.sql" ]; then
    docker exec -i -e PGPASSWORD=airutil_secure_vault_2026 airutil_db psql -U airutil -d airutil < "${RESTORE_SRC}/postgres_backup.sql"
    echo "    ✓ Relational database restored successfully."
else
    echo "    ✗ ERROR: postgres_backup.sql not found in ${RESTORE_SRC}!" >&2
    exit 1
fi

# 3. Restore /vault_storage Volume
echo "==> [3/3] Restoring airutil_data Storage Volume..."
if [ -f "${RESTORE_SRC}/vault_storage.tar.gz" ]; then
    docker run --rm \
        -v airutil_data:/target_data \
        -v "$(pwd)/${RESTORE_SRC}:/backup_src:ro" \
        alpine:latest \
        sh -c "rm -rf /target_data/* && tar -xzf /backup_src/vault_storage.tar.gz -C /target_data && chown -R 10001:10001 /target_data"
    echo "    ✓ Storage volume restored and permissions reset (10001:10001)."
else
    echo "    ✗ ERROR: vault_storage.tar.gz not found in ${RESTORE_SRC}!" >&2
    exit 1
fi

echo "======================================================================"
echo "AIRUTIL RESTORE COMPLETED SUCCESSFULLY"
echo "======================================================================"
