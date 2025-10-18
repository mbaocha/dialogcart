#!/bin/bash
# Helper script to view Luma API logs with pretty formatting
# Requires: jq (install with: apt install jq / brew install jq)

set -e

CONTAINER_NAME="luma-luma-api-1"
COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_YELLOW="\033[33m"
COLOR_RED="\033[31m"
COLOR_CYAN="\033[36m"
COLOR_MAGENTA="\033[35m"

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Install with:"
    echo "  Ubuntu/Debian: sudo apt install jq"
    echo "  macOS: brew install jq"
    exit 1
fi

# Function to colorize log level
colorize_level() {
    local level=$1
    case $level in
        "DEBUG") echo -e "${COLOR_CYAN}DEBUG${COLOR_RESET}" ;;
        "INFO") echo -e "${COLOR_GREEN}INFO${COLOR_RESET}" ;;
        "WARNING") echo -e "${COLOR_YELLOW}WARNING${COLOR_RESET}" ;;
        "ERROR") echo -e "${COLOR_RED}ERROR${COLOR_RESET}" ;;
        "CRITICAL") echo -e "${COLOR_MAGENTA}CRITICAL${COLOR_RESET}" ;;
        *) echo "$level" ;;
    esac
}

# Show usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -f, --follow         Follow log output (like tail -f)"
    echo "  -n, --lines NUM      Show last NUM lines (default: 50)"
    echo "  -l, --level LEVEL    Filter by log level (DEBUG, INFO, WARNING, ERROR)"
    echo "  -p, --path PATH      Filter by request path"
    echo "  -e, --errors         Show only errors"
    echo "  -s, --stats          Show request statistics"
    echo "  -r, --raw            Show raw JSON (no pretty formatting)"
    echo "  -h, --help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 -f                       # Follow logs"
    echo "  $0 -n 100                   # Show last 100 lines"
    echo "  $0 -l ERROR                 # Show only errors"
    echo "  $0 -p /extract              # Show only /extract requests"
    echo "  $0 -e                       # Show errors and exceptions"
    echo "  $0 -s                       # Show stats about requests"
    exit 0
}

# Parse arguments
FOLLOW=false
LINES=50
LEVEL=""
PATH_FILTER=""
ERRORS_ONLY=false
STATS=false
RAW=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow) FOLLOW=true; shift ;;
        -n|--lines) LINES=$2; shift 2 ;;
        -l|--level) LEVEL=$2; shift 2 ;;
        -p|--path) PATH_FILTER=$2; shift 2 ;;
        -e|--errors) ERRORS_ONLY=true; shift ;;
        -s|--stats) STATS=true; shift ;;
        -r|--raw) RAW=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# Show stats
if [ "$STATS" = true ]; then
    echo "Calculating request statistics..."
    docker logs $CONTAINER_NAME 2>&1 | \
        jq -r 'select(.method != null) | "\(.path) \(.status_code) \(.duration_ms)"' | \
        awk '{
            path[$1]++;
            total_time[$1]+=$3;
            if ($2 >= 400) errors[$1]++;
        }
        END {
            printf "%-30s %10s %10s %10s %15s\n", "PATH", "REQUESTS", "ERRORS", "AVG_TIME", "TOTAL_TIME";
            printf "%-30s %10s %10s %10s %15s\n", "----", "--------", "------", "--------", "----------";
            for (p in path) {
                printf "%-30s %10d %10d %10.2fms %15.2fms\n", 
                    p, path[p], errors[p]+0, total_time[p]/path[p], total_time[p];
            }
        }'
    exit 0
fi

# Build jq filter
JQ_FILTER="."

if [ -n "$LEVEL" ]; then
    JQ_FILTER="$JQ_FILTER | select(.level == \"$LEVEL\")"
fi

if [ -n "$PATH_FILTER" ]; then
    JQ_FILTER="$JQ_FILTER | select(.path == \"$PATH_FILTER\")"
fi

if [ "$ERRORS_ONLY" = true ]; then
    JQ_FILTER="$JQ_FILTER | select(.level == \"ERROR\" or .level == \"CRITICAL\" or .exception != null)"
fi

# Format output
if [ "$RAW" = true ]; then
    # Raw JSON output
    if [ "$FOLLOW" = true ]; then
        docker logs -f $CONTAINER_NAME 2>&1 | jq -r "$JQ_FILTER"
    else
        docker logs --tail $LINES $CONTAINER_NAME 2>&1 | jq -r "$JQ_FILTER"
    fi
else
    # Pretty formatted output
    FORMAT='"\(.timestamp // .ts) [\(.level)] \(.message) \(if .request_id then "[\(.request_id)]" else "" end) \(if .duration_ms then "(\(.duration_ms)ms)" else "" end)"'
    
    if [ "$FOLLOW" = true ]; then
        docker logs -f $CONTAINER_NAME 2>&1 | jq -r "$JQ_FILTER | $FORMAT"
    else
        docker logs --tail $LINES $CONTAINER_NAME 2>&1 | jq -r "$JQ_FILTER | $FORMAT"
    fi
fi

