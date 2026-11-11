#!/bin/bash
# Logical backup of the incidentsim database.
#
# Settings come from the environment (see /etc/incidentsim/backup.env):
#   BACKUP_DATABASE  database to dump (default: incidentsim)
#   BACKUP_DIR       directory for dump files (required)
#   BACKUP_KEEP      number of newest dumps to keep; 0 keeps every dump
set -euo pipefail

database="${BACKUP_DATABASE:-incidentsim}"
backup_dir="${BACKUP_DIR:?BACKUP_DIR is not set}"
keep="${BACKUP_KEEP:-0}"

if ! [[ "$keep" =~ ^[0-9]+$ ]]; then
    echo "BACKUP_KEEP must be a non-negative integer, got '$keep'" >&2
    exit 2
fi

mkdir -p -- "$backup_dir"
target="$backup_dir/$database-$(date -u +%Y%m%dT%H%M%S.%3NZ).sql"

pg_dump --no-password --format=plain --file="$target" "$database"
echo "backup written: $target ($(stat --format=%s -- "$target") bytes)"

if (( keep > 0 )); then
    # File names embed a sortable UTC timestamp, so glob order is age order.
    shopt -s nullglob
    dumps=("$backup_dir/$database"-*.sql)
    excess=$(( ${#dumps[@]} - keep ))
    if (( excess > 0 )); then
        rm -f -- "${dumps[@]:0:excess}"
        echo "rotation removed $excess old backup(s), kept $keep"
    fi
fi
