#!/usr/bin/env bash
#
# EchoThink ClawCluster Backup Script
#
# Creates a compressed backup of ClawCluster Docker volumes and optionally
# includes the local .env file.
#
# Usage:
#   ./scripts/backup.sh [backup-directory]
#   ./scripts/backup.sh --include-secrets [backup-directory]
#   ./scripts/backup.sh --restore <backup-archive>
#
# Examples:
#   ./scripts/backup.sh
#   ./scripts/backup.sh /mnt/backups/clawcluster
#   ./scripts/backup.sh --include-secrets
#   ./scripts/backup.sh --restore ./backups/clawcluster_backup_20260313_120000.tar.gz
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

VOLUMES=(
    clawcluster_higress_data
    clawcluster_tuwunel_data
    clawcluster_hiclaw_manager_data
    clawcluster_planner_worker_data
    clawcluster_workflow_worker_data
    clawcluster_coding_worker_data
    clawcluster_qa_worker_data
    clawcluster_knowledge_worker_data
)

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_ROOT="${BACKUP_ROOT:-./backups}"

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

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

INCLUDE_SECRETS=false
RESTORE_MODE=false
RESTORE_ARCHIVE=""
BACKUP_DIR=""
ARCHIVE_PATH=""

VOLUMES_BACKED_UP=0
VOLUMES_SKIPPED=0
VOLUME_FAILURES=0
ENV_INCLUDED=false

RESTORED_COUNT=0
RESTORE_SKIPPED=0
RESTORE_FAILURES=0
ENV_RESTORED=false

usage() {
    cat <<EOF
EchoThink ClawCluster Backup Script

Usage:
  $0 [backup-directory]
  $0 --include-secrets [backup-directory]
  $0 --restore <backup-archive>

Options:
  --include-secrets  Include ${PROJECT_DIR}/.env in the backup archive
  --restore          Restore Docker volumes from the given backup archive
  -h, --help         Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --include-secrets)
            INCLUDE_SECRETS=true
            shift
            ;;
        --restore)
            RESTORE_MODE=true
            RESTORE_ARCHIVE="${2:-}"
            if [[ -z "$RESTORE_ARCHIVE" ]]; then
                error "Usage: $0 --restore <backup-archive>"
                exit 1
            fi
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            BACKUP_ROOT="$1"
            shift
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

ensure_docker_available() {
    if ! docker info >/dev/null 2>&1; then
        error "Docker daemon is not available"
        exit 1
    fi
}

write_manifest() {
    cat > "${BACKUP_DIR}/manifest.txt" <<EOF
created_at=$(date '+%Y-%m-%d %H:%M:%S')
include_secrets=${INCLUDE_SECRETS}
env_source=${ENV_SOURCE}
volumes=${VOLUMES[*]}
EOF
}

backup_volume() {
    local volume="$1"
    local archive_file="${BACKUP_DIR}/volumes/${volume}.tar.gz"

    info "  Backing up volume: $volume"

    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
        warn "  Volume '$volume' does not exist, skipping"
        VOLUMES_SKIPPED=$((VOLUMES_SKIPPED + 1))
        return
    fi

    if docker run --rm -v "${volume}:/data" alpine tar czf - /data > "$archive_file" 2>/dev/null; then
        local archive_size
        archive_size=$(du -h "$archive_file" 2>/dev/null | cut -f1)
        success "  $volume: $archive_size"
        VOLUMES_BACKED_UP=$((VOLUMES_BACKED_UP + 1))
    else
        rm -f "$archive_file"
        error "  Failed to back up volume: $volume"
        VOLUME_FAILURES=$((VOLUME_FAILURES + 1))
    fi
}

backup_env_file() {
    if [[ "$INCLUDE_SECRETS" == false ]]; then
        info "Skipping .env backup (use --include-secrets to include it)"
        return
    fi

    if [[ -f "$ENV_FILE" ]]; then
        cp "$ENV_FILE" "${BACKUP_DIR}/.env"
        success "Included .env in backup archive"
        ENV_INCLUDED=true
    else
        warn "Requested .env backup, but ${ENV_FILE} was not found"
    fi
}

compress_backup() {
    info "Compressing backup..."

    tar -czf "$ARCHIVE_PATH" -C "$BACKUP_ROOT" "$TIMESTAMP"

    local archive_size
    archive_size=$(du -h "$ARCHIVE_PATH" | cut -f1)
    success "Compressed archive: $ARCHIVE_PATH ($archive_size)"

    rm -rf "$BACKUP_DIR"
    info "Removed uncompressed backup directory"
}

restore_volume() {
    local volume="$1"
    local archive_file="$2"

    info "  Restoring volume: $volume"

    docker volume create "$volume" >/dev/null 2>&1

    if docker run --rm -i -v "${volume}:/data" alpine sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true; tar xzf - -C /' < "$archive_file" >/dev/null 2>&1; then
        local archive_size
        archive_size=$(du -h "$archive_file" 2>/dev/null | cut -f1)
        success "  $volume: restored from $archive_size archive"
        RESTORED_COUNT=$((RESTORED_COUNT + 1))
    else
        error "  Failed to restore volume: $volume"
        RESTORE_FAILURES=$((RESTORE_FAILURES + 1))
    fi
}

restore_env_file() {
    local restore_dir="$1"
    local restored_env_file="${restore_dir}/.env"

    if [[ ! -f "$restored_env_file" ]]; then
        return
    fi

    if [[ -f "$ENV_FILE" ]]; then
        local env_backup
        env_backup="${PROJECT_DIR}/.env.pre_restore_${TIMESTAMP}"
        cp "$ENV_FILE" "$env_backup"
        warn "Existing .env backed up to $env_backup"
    fi

    cp "$restored_env_file" "$ENV_FILE"
    success "Restored .env to ${ENV_FILE}"
    ENV_RESTORED=true
}

print_backup_summary() {
    local backup_end
    backup_end=$(date +%s)
    local duration=$((backup_end - BACKUP_START))

    echo ""
    echo -e "${BOLD}=== Backup Summary ===${NC}"
    echo "  Date:     $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Duration: ${duration}s"
    echo "  Archive:  $ARCHIVE_PATH"

    if [[ -f "$ARCHIVE_PATH" ]]; then
        local archive_size
        archive_size=$(du -h "$ARCHIVE_PATH" | cut -f1)
        echo "  Size:     $archive_size"
    fi

    echo ""
    echo "  Volumes backed up: $VOLUMES_BACKED_UP"
    echo "  Volumes skipped:   $VOLUMES_SKIPPED"
    echo "  Volume failures:   $VOLUME_FAILURES"
    echo "  Included .env:     $([[ "$ENV_INCLUDED" == true ]] && echo yes || echo no)"
    echo ""

    if [[ $VOLUME_FAILURES -eq 0 && $VOLUMES_BACKED_UP -gt 0 ]]; then
        echo -e "${GREEN}${BOLD}Backup complete.${NC}"
    else
        echo -e "${YELLOW}${BOLD}Backup completed with warnings.${NC}"
    fi
}

print_restore_summary() {
    local restore_end
    restore_end=$(date +%s)
    local duration=$((restore_end - RESTORE_START))

    echo ""
    echo -e "${BOLD}=== Restore Summary ===${NC}"
    echo "  Date:       $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Duration:   ${duration}s"
    echo "  Archive:    $RESTORE_ARCHIVE"
    echo ""
    echo "  Volumes restored: $RESTORED_COUNT"
    echo "  Volumes skipped:  $RESTORE_SKIPPED"
    echo "  Restore failures: $RESTORE_FAILURES"
    echo "  Restored .env:    $([[ "$ENV_RESTORED" == true ]] && echo yes || echo no)"
    echo ""

    if [[ $RESTORE_FAILURES -eq 0 && $RESTORED_COUNT -gt 0 ]]; then
        echo -e "${GREEN}${BOLD}Restore complete.${NC}"
    else
        echo -e "${YELLOW}${BOLD}Restore completed with warnings.${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Backup mode
# ---------------------------------------------------------------------------

run_backup() {
    mkdir -p "$BACKUP_ROOT"

    BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
    ARCHIVE_PATH="${BACKUP_ROOT}/clawcluster_backup_${TIMESTAMP}.tar.gz"

    mkdir -p "${BACKUP_DIR}/volumes"

    echo -e "${BOLD}EchoThink ClawCluster Backup${NC}"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "Backup directory: $BACKUP_DIR"
    echo ""

    BACKUP_START=$(date +%s)

    if [[ "$ENV_SOURCE" == ".env.example" ]]; then
        warn "Loaded .env.example because .env was not found"
    fi

    info "Starting volume backup..."
    for volume in "${VOLUMES[@]}"; do
        backup_volume "$volume"
    done

    echo ""
    backup_env_file
    write_manifest

    echo ""
    compress_backup
    print_backup_summary

    if [[ $VOLUME_FAILURES -gt 0 || $VOLUMES_BACKED_UP -eq 0 ]]; then
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Restore mode
# ---------------------------------------------------------------------------

run_restore() {
    local extract_dir
    local restore_dir

    if [[ ! -f "$RESTORE_ARCHIVE" ]]; then
        error "Backup archive not found: $RESTORE_ARCHIVE"
        exit 1
    fi

    echo -e "${BOLD}EchoThink ClawCluster Restore${NC}"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "Restore archive: $RESTORE_ARCHIVE"
    echo ""

    RESTORE_START=$(date +%s)

    info "Checking archive integrity..."
    if tar -tzf "$RESTORE_ARCHIVE" >/dev/null 2>&1; then
        success "Archive is valid and readable"
    else
        error "Archive is corrupt or not a valid tar.gz file"
        exit 1
    fi

    extract_dir=$(mktemp -d)
    trap "rm -rf '$extract_dir'" EXIT

    info "Extracting archive..."
    tar -xzf "$RESTORE_ARCHIVE" -C "$extract_dir"
    success "Archive extracted"

    restore_dir=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -1)
    if [[ -z "$restore_dir" ]]; then
        error "Unable to determine extracted backup directory"
        exit 1
    fi

    for volume in "${VOLUMES[@]}"; do
        local volume_archive="${restore_dir}/volumes/${volume}.tar.gz"

        if [[ -f "$volume_archive" ]]; then
            restore_volume "$volume" "$volume_archive"
        else
            warn "  Volume archive missing for '$volume', skipping"
            RESTORE_SKIPPED=$((RESTORE_SKIPPED + 1))
        fi
    done

    echo ""
    restore_env_file "$restore_dir"
    print_restore_summary

    if [[ $RESTORE_FAILURES -gt 0 || $RESTORED_COUNT -eq 0 ]]; then
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    require_command docker
    require_command tar
    ensure_docker_available

    if [[ "$RESTORE_MODE" == true ]]; then
        run_restore
    else
        run_backup
    fi
}

main
