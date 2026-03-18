#!/usr/bin/env bash
# clone-prune.sh — Weekly thought archive pruner.
# Runs Sunday 3am CT (09:00 UTC) via cron.
# Moves thoughts older than 7 days into weekly archive files.

set -uo pipefail

CLONE_DIR="/home/ubuntu/clone"
THOUGHTS_DIR="${CLONE_DIR}/thoughts"
ARCHIVE_DIR="${THOUGHTS_DIR}/archive"
RELAY_DIR="/home/ubuntu/relay"
LOG_FILE="${RELAY_DIR}/logs/clone-prune.log"

mkdir -p "${ARCHIVE_DIR}" "${RELAY_DIR}/logs"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# Find thought files older than 7 days (top-level only, not archive/)
old_files=$(find "${THOUGHTS_DIR}" -maxdepth 1 -name '*.md' -type f -mtime +7 2>/dev/null | sort)

if [[ -z "${old_files}" ]]; then
    log "SKIP: no thoughts older than 7 days"
    exit 0
fi

count=$(echo "${old_files}" | wc -l)
log "START: archiving ${count} thoughts"

# Group by ISO week and concatenate
while IFS= read -r file; do
    basename=$(basename "${file}")
    # Extract date from filename (format: 2026-03-17T08:00.md)
    file_date="${basename%%T*}"
    if [[ -z "${file_date}" ]] || [[ "${file_date}" == "${basename}" ]]; then
        # Fallback: use file modification date
        file_date=$(date -r "${file}" +%Y-%m-%d 2>/dev/null || continue)
    fi

    # Get ISO week number
    week_label=$(date -d "${file_date}" +%Y-W%V 2>/dev/null || date -d "${file_date}" +%Y-W%U 2>/dev/null || echo "unknown")
    archive_file="${ARCHIVE_DIR}/${week_label}.md"

    # Append with separator
    echo "" >> "${archive_file}"
    echo "---" >> "${archive_file}"
    echo "# ${basename}" >> "${archive_file}"
    echo "" >> "${archive_file}"
    cat "${file}" >> "${archive_file}"

    rm "${file}"
    log "ARCHIVED: ${basename} → ${week_label}.md"
done <<< "${old_files}"

log "DONE: ${count} thoughts archived"
