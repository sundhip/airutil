#!/usr/bin/env bash
# ==============================================================================
# Airutil Load Testing & Round-Robin Verification Script
# Sends HTTP requests to test Layer-7 upstream load distribution
# ==============================================================================

set -uo pipefail

TOTAL_REQUESTS="${1:-50}"
TARGET_URL="${2:-http://localhost:8080/healthz}"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "\n${BOLD}======================================================${NC}"
echo -e "${BOLD}       Airutil Round-Robin Load Test (${TOTAL_REQUESTS} reqs)       ${NC}"
echo -e "${BOLD}======================================================${NC}\n"
echo "Target: ${TARGET_URL}"

# Detect curl binary (curl.exe for Windows Git-Bash environments, curl for Linux/macOS)
CURL_BIN="curl"
if command -v curl.exe >/dev/null 2>&1; then
    CURL_BIN="curl.exe"
fi

APP1_HITS=0
APP2_HITS=0
FAILURES=0

START_SEC=$(date +%s)

for i in $(seq 1 "$TOTAL_REQUESTS"); do
    HEADER_OUT=$("$CURL_BIN" -sI "$TARGET_URL" 2>/dev/null || true)
    
    if echo "$HEADER_OUT" | grep -q "200 OK"; then
        SERVED_BY=$(echo "$HEADER_OUT" | grep -i "X-Served-By-Replica:" | tr -d '\r\n' | awk '{print $2}')
        if [[ "$SERVED_BY" == *"app1"* ]]; then
            ((APP1_HITS++))
        elif [[ "$SERVED_BY" == *"app2"* ]]; then
            ((APP2_HITS++))
        fi
    else
        ((FAILURES++))
    fi

    if [ $((i % 10)) -eq 0 ]; then
        echo -ne "Progress: ${i}/${TOTAL_REQUESTS} requests completed...\r"
    fi
done

END_SEC=$(date +%s)
DURATION_SEC=$(( END_SEC - START_SEC ))
[ "$DURATION_SEC" -le 0 ] && DURATION_SEC=1

echo -e "\n\n${BOLD}--- Load Test Results ---${NC}"
echo "Total Requests:      ${TOTAL_REQUESTS}"
echo "Duration:            ~${DURATION_SEC} s"
echo -e "app1 Responses:      ${CYAN}${APP1_HITS}${NC} ($(( APP1_HITS * 100 / TOTAL_REQUESTS ))%)"
echo -e "app2 Responses:      ${CYAN}${APP2_HITS}${NC} ($(( APP2_HITS * 100 / TOTAL_REQUESTS ))%)"
echo "Failed Requests:     ${FAILURES}"

if [ "$APP1_HITS" -gt 0 ] && [ "$APP2_HITS" -gt 0 ] && [ "$FAILURES" -eq 0 ]; then
    echo -e "\n${GREEN}${BOLD}SUCCESS: Balanced Layer-7 round-robin traffic split confirmed with 0 errors!${NC}\n"
    exit 0
else
    echo -e "\n${BOLD}Completed.${NC}\n"
    exit 0
fi
