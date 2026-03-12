#!/usr/bin/env bash
#
# EchoThink ClawCluster MinIO Bucket Setup
#
# Initializes the shared MinIO bucket and required placeholder objects.
#
# Usage:
#   ./scripts/setup-minio-buckets.sh
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
# Configuration
# ---------------------------------------------------------------------------

MINIO_ALIAS="clawcluster"
MINIO_BUCKET="${MINIO_HICLAW_BUCKET:-clawcluster-sharedfs}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-}"

PLACEHOLDER_OBJECTS=(
    hiclaw-storage/agents/.keep
    hiclaw-storage/shared/tasks/.keep
)

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

info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

PASSED=0
FAILED=0
WARNINGS=0

usage() {
    cat <<EOF
EchoThink ClawCluster MinIO Bucket Setup

Usage:
  $0

Options:
  -h, --help    Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            echo ""
            usage
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

require_command() {
    local command_name="$1"

    if ! command -v "$command_name" >/dev/null 2>&1; then
        error "Required command not found: $command_name"
        exit 1
    fi
}

mark_success() {
    PASSED=$((PASSED + 1))
    success "$1"
}

mark_failure() {
    FAILED=$((FAILED + 1))
    error "$1"
}

mark_warning() {
    WARNINGS=$((WARNINGS + 1))
    warn "$1"
}

validate_configuration() {
    local valid=true

    if [[ -z "$MINIO_ENDPOINT" ]]; then
        mark_failure "MINIO_ENDPOINT is not set"
        valid=false
    fi

    if [[ -z "$MINIO_ACCESS_KEY" ]]; then
        mark_failure "MINIO_ACCESS_KEY is not set"
        valid=false
    fi

    if [[ -z "$MINIO_SECRET_KEY" ]]; then
        mark_failure "MINIO_SECRET_KEY is not set"
        valid=false
    fi

    if [[ "$valid" == false ]]; then
        return 1
    fi
}

configure_alias() {
    info "Configuring MinIO alias..."

    if mc alias set "$MINIO_ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; then
        mark_success "Configured alias '$MINIO_ALIAS'"
    else
        mark_failure "Failed to configure alias '$MINIO_ALIAS'"
        return 1
    fi
}

ensure_bucket() {
    info "Ensuring bucket exists: $MINIO_BUCKET"

    if mc mb --ignore-existing "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1; then
        mark_success "Bucket '$MINIO_BUCKET' is ready"
    else
        mark_failure "Failed to create or access bucket '$MINIO_BUCKET'"
        return 1
    fi
}

set_private_policy() {
    info "Setting private access policy..."

    if mc anonymous set none "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1; then
        mark_success "Applied private access policy to '$MINIO_BUCKET'"
    elif mc policy set private "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1; then
        mark_success "Applied private access policy to '$MINIO_BUCKET'"
    else
        mark_failure "Failed to set private access policy on '$MINIO_BUCKET'"
        return 1
    fi
}

create_placeholder_objects() {
    local placeholder_file
    placeholder_file=$(mktemp)

    info "Creating placeholder objects..."

    for object_path in "${PLACEHOLDER_OBJECTS[@]}"; do
        if mc cp "$placeholder_file" "${MINIO_ALIAS}/${MINIO_BUCKET}/${object_path}" >/dev/null 2>&1; then
            mark_success "Created placeholder object '${object_path}'"
        else
            mark_failure "Failed to create placeholder object '${object_path}'"
        fi
    done

    rm -f "$placeholder_file"
}

print_summary() {
    echo ""
    echo -e "${BOLD}=== MinIO Setup Summary ===${NC}"
    echo "  Alias:    $MINIO_ALIAS"
    echo "  Endpoint: $MINIO_ENDPOINT"
    echo "  Bucket:   $MINIO_BUCKET"
    echo "  Passed:   $PASSED"
    echo "  Failed:   $FAILED"
    echo "  Warnings: $WARNINGS"
    echo ""

    if [[ $FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}MinIO bucket setup complete.${NC}"
    else
        echo -e "${RED}${BOLD}MinIO bucket setup failed.${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    require_command mc

    if [[ "$ENV_SOURCE" == ".env.example" ]]; then
        mark_warning "Loaded .env.example because .env was not found"
    fi

    validate_configuration || {
        print_summary
        exit 1
    }

    configure_alias || {
        print_summary
        exit 1
    }

    ensure_bucket || {
        print_summary
        exit 1
    }

    set_private_policy || {
        print_summary
        exit 1
    }

    create_placeholder_objects
    print_summary

    if [[ $FAILED -gt 0 ]]; then
        exit 1
    fi
}

main
