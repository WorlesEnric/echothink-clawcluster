#!/usr/bin/env bash
#
# EchoThink ClawCluster Health Check
#
# Checks all service HTTP endpoints and prints colored status output.
# Exit code 0 if all services are healthy, non-zero otherwise.
#
# Usage:
#   ./scripts/healthcheck.sh
#   ./scripts/healthcheck.sh --quiet
#

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE_FILE="${PROJECT_DIR}/.env.example"
ENV_SOURCE=".env"

if ! source "$ENV_FILE" 2>/dev/null; then
    source "$ENV_EXAMPLE_FILE"
    ENV_SOURCE=".env.example"
fi

# ---------------------------------------------------------------------------
# Configuration -- service endpoints
# ---------------------------------------------------------------------------

HIGRESS_HTTP_PORT="${HIGRESS_HTTP_PORT:-8080}"
TUWUNEL_PORT="${TUWUNEL_PORT:-6167}"
ELEMENT_WEB_PORT="${ELEMENT_WEB_PORT:-80}"
HICLAW_MANAGER_PORT="${HICLAW_MANAGER_PORT:-8088}"
INTAKE_BRIDGE_PORT="${INTAKE_BRIDGE_PORT:-8100}"
PUBLISHER_BRIDGE_PORT="${PUBLISHER_BRIDGE_PORT:-8101}"
POLICY_BRIDGE_PORT="${POLICY_BRIDGE_PORT:-8102}"
OBSERVABILITY_BRIDGE_PORT="${OBSERVABILITY_BRIDGE_PORT:-8103}"

CURL_MAX_TIME="${CURL_MAX_TIME:-5}"

# ---------------------------------------------------------------------------
# Color output
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RESET='\033[0m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC="$RESET"

QUIET=false
PASSED=0
FAILED=0
WARNINGS=0

usage() {
    cat <<EOF
EchoThink ClawCluster Health Check

Usage:
  $0 [--quiet]

Options:
  --quiet       Suppress per-check output and print summary only
  -h, --help    Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quiet)
            QUIET=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}[ERROR]${RESET} Unknown argument: $1"
            echo ""
            usage
            exit 1
            ;;
    esac
done

print_header() {
    if [[ "$QUIET" == false ]]; then
        echo ""
        echo -e "${BOLD}${CYAN}=== $1 ===${NC}"
    fi
}

print_pass() {
    PASSED=$((PASSED + 1))
    if [[ "$QUIET" == false ]]; then
        echo -e "  ${GREEN}[PASS]${NC}  $1"
    fi
}

print_fail() {
    FAILED=$((FAILED + 1))
    if [[ "$QUIET" == false ]]; then
        echo -e "  ${RED}[FAIL]${NC}  $1"
    fi
}

print_warn() {
    WARNINGS=$((WARNINGS + 1))
    if [[ "$QUIET" == false ]]; then
        echo -e "  ${YELLOW}[WARN]${NC}  $1"
    fi
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

require_command() {
    local command_name="$1"

    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo -e "${RED}[ERROR]${RESET} Required command not found: $command_name"
        exit 1
    fi
}

check_http_endpoint() {
    local name="$1"
    local url="$2"
    local error_output=""

    if error_output=$(curl -fsS --max-time "$CURL_MAX_TIME" -o /dev/null "$url" 2>&1); then
        print_pass "$name is responding ($url)"
    else
        if [[ -n "$error_output" ]]; then
            print_fail "$name check failed ($url): $error_output"
        else
            print_fail "$name check failed ($url)"
        fi
    fi
}

print_summary() {
    local total_checks
    total_checks=$((PASSED + FAILED))

    echo ""
    echo -e "${BOLD}=== Health Check Summary ===${NC}"
    echo "  Total:    $total_checks"
    echo "  Passed:   $PASSED"
    echo "  Failed:   $FAILED"
    echo "  Warnings: $WARNINGS"
    echo ""

    if [[ $FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}All service health checks passed.${NC}"
    else
        echo -e "${RED}${BOLD}One or more service health checks failed.${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    require_command curl

    if [[ "$ENV_SOURCE" == ".env.example" ]]; then
        print_warn "Loaded .env.example because .env was not found"
    fi

    print_header "Gateway"
    check_http_endpoint "higress" "http://localhost:${HIGRESS_HTTP_PORT}/health"

    print_header "Core Services"
    check_http_endpoint "tuwunel" "http://localhost:${TUWUNEL_PORT}/_matrix/client/versions"
    check_http_endpoint "element-web" "http://localhost:${ELEMENT_WEB_PORT}/"
    check_http_endpoint "hiclaw-manager" "http://localhost:${HICLAW_MANAGER_PORT}/health"

    print_header "Bridge Services"
    check_http_endpoint "intake-bridge" "http://localhost:${INTAKE_BRIDGE_PORT}/health"
    check_http_endpoint "publisher-bridge" "http://localhost:${PUBLISHER_BRIDGE_PORT}/health"
    check_http_endpoint "policy-bridge" "http://localhost:${POLICY_BRIDGE_PORT}/health"
    check_http_endpoint "observability-bridge" "http://localhost:${OBSERVABILITY_BRIDGE_PORT}/health"

    print_summary

    if [[ $FAILED -gt 0 ]]; then
        exit 1
    fi
}

main
