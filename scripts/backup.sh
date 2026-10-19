#!/usr/bin/env bash
# ==============================================================================
# Airutil Disaster Recovery: Automated Backup Script
#
# OS Concepts Demonstrated:
# 1. Consistent Snapshotting: pg_dump creates an atomic, transactionally
#    consistent snapshot of relational database tables without locking reads.
# 2. VFS Volume Archival: tar streams container volume blocks directly into
#    a compressed gzip archive.
# 3. Integrity Checksums: SHA-256 hashes generated for disaster recovery verification.
# ==============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
TARGET_DIR="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "${TARGET_DIR}"

echo "======================================================================"
echo "AIRUTIL BACKUP INITIALIZATION [${TIMESTAMP}]"
echo "Target directory: ${TARGET_DIR}"
echo "======================================================================"

# 1. Backup PostgreSQL Database
echo "==> [1/3] Dumping PostgreSQL Database (airutil_db)..."
if docker ps --format '{{.Names}}' | grep -q "^airutil_db$"; then
    docker exec -e PGPASSWORD=airutil_secure_vault_2026 airutil_db pg_dump -U airutil -d airutil --clean --if-exists > "${TARGET_DIR}/postgres_backup.sql"
    echo "    ✓ Database dumped successfully ($(wc -c < "${TARGET_DIR}/postgres_backup.sql") bytes)"
else
    echo "    ✗ ERROR: Container airutil_db is not running!" >&2
    exit 1
fi

# 2. Backup /vault_storage Shared Docker Volume
echo "==> [2/3] Archiving airutil_data Persistent Volume..."
if docker volume inspect airutil_data > /dev/null 2>&1; then
    docker run --rm \
        -v airutil_data:/source_data:ro \
        -v "$(pwd)/${TARGET_DIR}:/backup_target" \
        alpine:latest \
        tar -czf "/backup_target/vault_storage.tar.gz" -C /source_data .
    echo "    ✓ Storage volume archived successfully ($(du -h "${TARGET_DIR}/vault_storage.tar.gz" | cut -f1))"
else
    echo "    ✗ ERROR: Volume airutil_data does not exist!" >&2
    exit 1
fi

# 3. Generate SHA-256 Integrity Hashes
echo "==> [3/3] Generating SHA-256 Checksums..."
(
    cd "${TARGET_DIR}"
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum postgres_backup.sql vault_storage.tar.gz > SHA256SUMS
    else
        shasum -a 256 postgres_backup.sql vault_storage.tar.gz > SHA256SUMS
    fi
)
echo "    ✓ Integrity checksums written to ${TARGET_DIR}/SHA256SUMS"

echo "======================================================================"
echo "AIRUTIL BACKUP COMPLETED SUCCESSFULLY"
echo "Location: ${TARGET_DIR}"
echo "Contents:"
ls -lh "${TARGET_DIR}"
echo "======================================================================"
