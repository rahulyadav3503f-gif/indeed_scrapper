#!/usr/bin/env bash
#
# reset.sh -- return the Indeed scraper to a clean, first-run state.
#
#   ./reset.sh              show what would go, ask, then remove it
#   ./reset.sh --dry-run    show what would go and stop
#   ./reset.sh --backup     move it into reset_backup_<timestamp>/ instead
#   ./reset.sh --yes        skip the confirmation (for scripts)
#   ./reset.sh --force      reset even while the scraper is running (unsafe)
#
# WHAT IT REMOVES
#   every file the scraper generates at run time: the checkpoints, the
#   failed-page queue, the collected jobs, the Excel rotation files, the
#   launcher's log and pid file, and the browser's demonstration page cache.
#
# WHAT IT NEVER TOUCHES
#   indeed_auth.json (your saved login -- deleting it means logging in
#   again), any .py source, source_file/, the tests, the docs, the ./indeed
#   launcher, the service files, venv/ and .git. The list is an explicit
#   allowlist, not a wildcard sweep, so a file it does not know about is left
#   alone rather than guessed at.
#
# AFTERWARDS
#   just start the scraper. Every file below is recreated on demand:
#   crawl_cycle_state.json on the first cycle, state_city.json and
#   state_role.json on the first search, cycle_state.json on the first Excel
#   rotation, failed_pages.json on the first failure, and the .xlsx files on
#   the first saved job.
#
set -uo pipefail

# Work on THIS script's directory, never the caller's. Running it from
# somewhere else can then never delete the wrong project.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

DRY_RUN=0; ASSUME_YES=0; DO_BACKUP=0; FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1 ;;
        -y|--yes)     ASSUME_YES=1 ;;
        -b|--backup)  DO_BACKUP=1 ;;
        -f|--force)   FORCE=1 ;;
        -h|--help)    sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "reset.sh: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
    shift
done

# --------------------------------------------------------------------------
# WHAT GETS REMOVED
# --------------------------------------------------------------------------
# Checkpoints and queues -- pure progress bookkeeping, no scraped data.
STATE_FILES=(
    crawl_cycle_state.json      # the authoritative crawl checkpoint
    cycle_state.json            # the Excel 100-row batch counter
    state_city.json             # readable "which city am I on" log
    state_role.json             # readable "which keyword am I on" log
    failed_pages.json           # the 403/429/5xx retry queue
    current_row.json            # not written by this project; legacy cleanup
)

# Collected jobs. THIS IS REAL DATA -- --backup keeps it.
DATA_FILES=(
    current_data.xlsx                 # the working feed
    indeed_it_jobs_by_city.xlsx       # the master list
)

# The ./indeed launcher's own bookkeeping.
RUNTIME_FILES=(
    indeed.log                  # the service / background log
    indeed.pid                  # the standalone-mode pid file
)

# Patterns: Excel rotation files, and the dot-prefixed temp files the atomic
# writers leave behind if they are killed mid-write.
GLOB_PATTERNS=(
    'current_data_cycle*.xlsx'
    '.*.tmp'
    '.*.tmp.xlsx'
)

DIRS=(
    __pycache__
    source_file/__pycache__
    tests/__pycache__
)

# Never removed, whatever else happens.
PROTECTED=(
    indeed_auth.json find_indeed_job.py save_indeed_login.py
    new_mylib.py target_audience_roles.py source_file
    requirements.txt .gitignore reset.sh
    indeed indeed.service install_service.sh
    tests docs venv .git
)

# --------------------------------------------------------------------------
# SAFETY
# --------------------------------------------------------------------------
if [ ! -f "find_indeed_job.py" ]; then
    echo "reset.sh: find_indeed_job.py is not in $PROJECT_DIR." >&2
    echo "          Refusing to delete anything from a directory that does" >&2
    echo "          not look like the scraper project." >&2
    exit 1
fi

# Which processes are actually RUNNING the scraper?
#
# `pgrep -f find_indeed_job.py` alone is too eager: it also matches this
# script, its parent shell, an editor, or any command line that merely
# mentions the filename -- which would refuse a perfectly safe reset. So each
# candidate's real command line is read back and must look like a Python
# process executing the script, and our own process and its parent are
# skipped outright.
RUNNING_PIDS=""
for candidate in $(pgrep -f '[f]ind_indeed_job\.py' 2>/dev/null); do
    [ "$candidate" = "$$" ] && continue
    [ "$candidate" = "${PPID:-0}" ] && continue
    candidate_cmd="$(tr '\0' ' ' < "/proc/$candidate/cmdline" 2>/dev/null)"
    case "$candidate_cmd" in
        *reset.sh*)                      continue ;;
        *python*find_indeed_job.py*)     RUNNING_PIDS="$RUNNING_PIDS $candidate" ;;
    esac
done
RUNNING_PIDS="${RUNNING_PIDS# }"
SERVICE_ACTIVE=0
if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet indeed 2>/dev/null && SERVICE_ACTIVE=1
fi

if { [ -n "${RUNNING_PIDS// /}" ] || [ "$SERVICE_ACTIVE" -eq 1 ]; } \
   && [ "$FORCE" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo
    if [ -n "${RUNNING_PIDS// /}" ]; then
        echo "  The scraper is still running (PID ${RUNNING_PIDS% })."
    else
        echo "  The 'indeed' service is still active."
    fi
    echo
    echo "  Resetting now would not work: the live process holds its state in"
    echo "  memory and rewrites these files within seconds, so you would end"
    echo "  up with a half-deleted mixture rather than a clean slate. Its"
    echo "  Chromium window would also keep writing into the Excel feed."
    echo
    echo "  Stop it first:"
    if [ -x "./indeed" ]; then
        echo "      ./indeed stop"
    else
        echo "      kill -INT ${RUNNING_PIDS% }"
    fi
    echo
    echo "  Then run ./reset.sh again. (--force overrides this, but do not.)"
    echo
    exit 1
fi

# --------------------------------------------------------------------------
# COLLECT
# --------------------------------------------------------------------------
is_protected() {
    local candidate="${1#./}"
    local keep
    for keep in "${PROTECTED[@]}"; do
        [ "$candidate" = "$keep" ] && return 0
    done
    # Earlier backups are results of this script; never eat them.
    case "$candidate" in
        reset_backup_*) return 0 ;;
    esac
    return 1
}

TARGETS=()
add_target() {
    local path="${1#./}"
    [ -e "$path" ] || return 0
    is_protected "$path" && return 0
    local seen
    for seen in ${TARGETS[@]+"${TARGETS[@]}"}; do
        [ "$seen" = "$path" ] && return 0
    done
    TARGETS+=("$path")
}

for f in "${STATE_FILES[@]}" "${DATA_FILES[@]}" "${RUNTIME_FILES[@]}"; do
    add_target "$f"
done
for pattern in "${GLOB_PATTERNS[@]}"; do
    shopt -s nullglob dotglob
    for match in $pattern; do add_target "$match"; done
    shopt -u nullglob dotglob
done
for d in "${DIRS[@]}"; do add_target "$d"; done

# The visible browser writes its demonstration pages into the system temp
# directory, not into the project, so they are collected separately. They are
# pure cache: nothing is ever backed up from here.
DEMO_DIRS=()
shopt -s nullglob
for demo in "${TMPDIR:-/tmp}"/indeed-demo-pages-*; do
    [ -d "$demo" ] && [ -O "$demo" ] && DEMO_DIRS+=("$demo")
done
shopt -u nullglob

if [ "${#TARGETS[@]}" -eq 0 ] && [ "${#DEMO_DIRS[@]}" -eq 0 ]; then
    echo
    echo "  Already clean -- nothing to reset in $PROJECT_DIR"
    echo "  Start the scraper and it will create everything it needs."
    echo
    exit 0
fi

# --------------------------------------------------------------------------
# SHOW
# --------------------------------------------------------------------------
human() { du -sh "$1" 2>/dev/null | cut -f1; }

echo
echo "  Project : $PROJECT_DIR"
if [ "$DO_BACKUP" -eq 1 ]; then
    echo "  Action  : MOVE to a backup folder"
elif [ "$DRY_RUN" -eq 1 ]; then
    echo "  Action  : dry run, nothing will change"
else
    echo "  Action  : DELETE permanently"
fi
echo
printf "  %-36s %8s  %s\n" "ITEM" "SIZE" "WHAT IT IS"
printf "  %-36s %8s  %s\n" "------------------------------------" "--------" "-------------------------"

describe() {
    case "$1" in
        crawl_cycle_state.json) echo "crawl checkpoint" ;;
        cycle_state.json)       echo "Excel batch counter" ;;
        state_city.json|state_role.json) echo "readable progress log" ;;
        failed_pages.json)      echo "403/429/5xx retry queue" ;;
        current_row.json)       echo "legacy row pointer" ;;
        current_data.xlsx)      echo "collected jobs (working)" ;;
        current_data_cycle*)    echo "collected jobs (rotated)" ;;
        indeed_it_jobs_by_city.xlsx) echo "master job list" ;;
        indeed.log)             echo "launcher / service log" ;;
        indeed.pid)             echo "background pid file" ;;
        */indeed-demo-pages-*)  echo "browser demo page cache" ;;
        __pycache__|*/__pycache__) echo "python bytecode cache" ;;
        .*)                     echo "interrupted atomic write" ;;
        *)                      echo "generated file" ;;
    esac
}

HAS_DATA=0
for t in ${TARGETS[@]+"${TARGETS[@]}"}; do
    printf "  %-36s %8s  %s\n" "$t" "$(human "$t")" "$(describe "$t")"
    case "$t" in
        *.xlsx) HAS_DATA=1 ;;
    esac
done
for t in ${DEMO_DIRS[@]+"${DEMO_DIRS[@]}"}; do
    printf "  %-36s %8s  %s\n" "$t" "$(human "$t")" "$(describe "$t")"
done
echo
echo "  Kept: indeed_auth.json (your login), all .py sources, source_file/,"
echo "        tests/, docs/, venv/, the ./indeed launcher, the service files"
echo "        and .git"
echo

if [ "$HAS_DATA" -eq 1 ] && [ "$DO_BACKUP" -eq 0 ]; then
    echo "  NOTE: the .xlsx files above are scraped jobs, not just bookkeeping."
    echo "        Run './reset.sh --backup' instead to keep a copy."
    echo
fi

[ "$DRY_RUN" -eq 1 ] && { echo "  Dry run -- nothing changed."; echo; exit 0; }

if [ "$ASSUME_YES" -eq 0 ]; then
    printf "  Reset these %d item(s)? [y/N] " \
        "$(( ${#TARGETS[@]} + ${#DEMO_DIRS[@]} ))"
    read -r REPLY </dev/tty 2>/dev/null || REPLY=""
    case "$REPLY" in
        [yY]|[yY][eE][sS]) ;;
        *) echo; echo "  Cancelled -- nothing was changed."; echo; exit 1 ;;
    esac
    echo
fi

# --------------------------------------------------------------------------
# DO IT
# --------------------------------------------------------------------------
FAILED=0
if [ "$DO_BACKUP" -eq 1 ]; then
    BACKUP_DIR="reset_backup_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR" || exit 1
    for t in ${TARGETS[@]+"${TARGETS[@]}"}; do
        if mv -- "$t" "$BACKUP_DIR"/ 2>/dev/null; then
            echo "  moved   $t"
        else
            echo "  FAILED  $t" >&2; FAILED=$((FAILED + 1))
        fi
    done
    echo
    echo "  Backup : $PROJECT_DIR/$BACKUP_DIR  ($(human "$BACKUP_DIR"))"
else
    for t in ${TARGETS[@]+"${TARGETS[@]}"}; do
        if rm -rf -- "$t" 2>/dev/null; then
            echo "  removed $t"
        else
            echo "  FAILED  $t" >&2; FAILED=$((FAILED + 1))
        fi
    done
fi

# Demonstration page caches are deleted in both modes -- they are rebuilt
# from the fixtures on the next run, so there is nothing to keep.
for t in ${DEMO_DIRS[@]+"${DEMO_DIRS[@]}"}; do
    if rm -rf -- "$t" 2>/dev/null; then
        echo "  removed $t"
    else
        echo "  FAILED  $t" >&2; FAILED=$((FAILED + 1))
    fi
done

# --------------------------------------------------------------------------
# VERIFY
# --------------------------------------------------------------------------
echo
MISSING=0
for keep in find_indeed_job.py new_mylib.py target_audience_roles.py \
            save_indeed_login.py source_file/indeed_client.py \
            source_file/indeed_extractor.py source_file/indeed_apply_url.py; do
    if [ ! -e "$keep" ]; then
        echo "  ERROR: $keep is missing after the reset!" >&2
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ] || [ "$FAILED" -gt 0 ]; then
    echo "  Reset finished with $FAILED failure(s) and $MISSING missing file(s)." >&2
    exit 1
fi

echo "  Reset complete. The project is back to its first-run state."
echo
if [ ! -e "indeed_auth.json" ]; then
    echo "  Next: python3 save_indeed_login.py     (no saved login found)"
fi
echo "  Next: start the scraper. It recreates its own files:"
echo "          crawl_cycle_state.json       on the first cycle"
echo "          state_city.json / state_role.json  on the first search"
echo "          cycle_state.json             on the first Excel rotation"
echo "          failed_pages.json            on the first failed page"
echo "          current_data.xlsx            on the first saved job"
echo "          indeed_it_jobs_by_city.xlsx  on the first saved page"
echo
if [ -x "./indeed" ]; then
    echo "      ./indeed start        (or: python3 find_indeed_job.py)"
else
    echo "      python3 find_indeed_job.py"
fi
echo
