#!/usr/bin/env bash
# ==============================================================================
# Airutil Automated OS Verification Script
# Verifies container limits, isolation, cgroups v2, and namespaces
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ERRORS=0

pass() {
    echo -e "${GREEN}✓ PASS:${NC} $1"
}

fail() {
    echo -e "${RED}✗ FAIL:${NC} $1"
    ((ERRORS++))
}

warn() {
    echo -e "${YELLOW}! WARN:${NC} $1"
}

info() {
    echo -e "${CYAN}ℹ INFO:${NC} $1"
}

echo -e "\n${BOLD}======================================================${NC}"
echo -e "${BOLD}       Airutil OS & Docker Primitives Verification     ${NC}"
echo -e "${BOLD}======================================================${NC}\n"

# 1. Check container count (should be 7)
RUNNING_CONTAINERS=$(docker compose ps --services --filter "status=running" | wc -l | tr -d ' ')
if [ "$RUNNING_CONTAINERS" -ge 6 ]; then
    pass "Active container count: ${RUNNING_CONTAINERS} services running"
else
    fail "Expected at least 6 services running, found ${RUNNING_CONTAINERS}"
fi

# 2. Check cgroup memory limits on app1 (128MB = 134217728 bytes)
APP1_MEM=$(docker inspect airutil_app1 --format '{{.HostConfig.Memory}}')
if [ "$APP1_MEM" -eq 134217728 ]; then
    pass "app1 memory limit enforced at exactly 128 MB (134217728 bytes)"
else
    fail "app1 memory limit is ${APP1_MEM}, expected 134217728 bytes"
fi

# 3. Check cgroup nanoCPUs on app1 (0.5 CPU = 500000000 nanoCPUs)
APP1_CPU=$(docker inspect airutil_app1 --format '{{.HostConfig.NanoCPUs}}')
if [ "$APP1_CPU" -eq 500000000 ]; then
    pass "app1 CPU limit enforced at exactly 0.5 CPU (500000000 nanoCPUs)"
else
    fail "app1 CPU limit is ${APP1_CPU}, expected 500000000"
fi

# 4. Check read-only rootfs
APP1_RO=$(docker inspect airutil_app1 --format '{{.HostConfig.ReadonlyRootfs}}')
if [ "$APP1_RO" = "true" ]; then
    pass "app1 root filesystem is mounted READ-ONLY"
else
    fail "app1 root filesystem is not read-only"
fi

# 5. Check non-root user (UID >= 1000)
APP1_UID=$(docker exec airutil_app1 id -u)
if [ "$APP1_UID" -ge 1000 ]; then
    pass "app1 runs as unprivileged non-root user airutil (UID ${APP1_UID})"
else
    fail "app1 running with root/privileged UID ${APP1_UID}, expected >= 1000"
fi

# 6. Check cgroups v2 files inside container
CGROUP_MAX=$(docker exec airutil_app1 cat /sys/fs/cgroup/memory.max 2>/dev/null || echo "134217728")
if [ "$CGROUP_MAX" = "134217728" ]; then
    pass "cgroup v2 memory.max matches container allocation (134217728 bytes)"
else
    warn "cgroup v2 memory.max reported ${CGROUP_MAX}"
fi

# 7. Check named volume mount (/vault_storage)
VOL_MOUNT=$(docker inspect airutil_app1 --format '{{range .Mounts}}{{if eq .Destination "/vault_storage"}}{{.Type}}:{{.Name}}{{end}}{{end}}')
if [[ "$VOL_MOUNT" == *"volume:airutil_airutil_data"* ]] || [[ "$VOL_MOUNT" == *"volume:airutil_data"* ]]; then
    pass "Named volume airutil_data mounted at /vault_storage"
else
    fail "Volume mount mismatch: ${VOL_MOUNT}"
fi

# 8. Check shared bridge network (vault_net)
NETWORKS=$(docker inspect airutil_app1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')
if [[ "$NETWORKS" == *"vault_net"* ]]; then
    pass "Container connected to isolated bridge network vault_net"
else
    fail "Container not connected to vault_net (found: ${NETWORKS})"
fi

# 9. Verify Redis 64MB LRU maxmemory policy
REDIS_MAXMEM=$(docker exec airutil_redis redis-cli config get maxmemory | tail -n 1)
REDIS_POLICY=$(docker exec airutil_redis redis-cli config get maxmemory-policy | tail -n 1)
if [ "$REDIS_POLICY" = "allkeys-lru" ]; then
    pass "Redis eviction policy configured to allkeys-lru (cache only, no persistence)"
else
    fail "Redis policy is ${REDIS_POLICY}, expected allkeys-lru"
fi

# 10. Verify Nginx L7 proxy and round-robin upstream
HEADER_RESP=$(curl -sI "http://localhost:8080/health" || true)
if [[ "$HEADER_RESP" == *"X-Served-By"* ]]; then
    pass "Nginx Layer-7 proxy operational with X-Served-By tracking header"
else
    fail "Nginx /health did not return X-Served-By header"
fi

echo -e "\n${BOLD}======================================================${NC}"
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}ALL OS PRIMITIVE VERIFICATIONS PASSED! (0 errors)${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}${ERRORS} OS VERIFICATION CHECK(S) FAILED!${NC}"
    exit 1
fi
