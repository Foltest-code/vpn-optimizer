#!/bin/sh
# VPN Optimizer delay scan v2.5.1

umask 077
CONFIG="/opt/etc/vpn-optimizer.conf"
CONFIG_VALUES=$(/opt/bin/python3 /opt/lib/vpn-optimizer/common.py --shell "$CONFIG") || exit 1
# Read validated data literally: no source/eval or shell expansion of values.
while IFS='=' read -r CONFIG_KEY CONFIG_VALUE
do
    case "$CONFIG_KEY" in
        SUB_ID) SUB_ID=$CONFIG_VALUE ;;
        SELECTOR) SELECTOR=$CONFIG_VALUE ;;
        AWGM_URL) AWGM_URL=$CONFIG_VALUE ;;
        CLASH_URL) CLASH_URL=$CONFIG_VALUE ;;
        PROXY_URL) PROXY_URL=$CONFIG_VALUE ;;
        NIGHT_HOUR) NIGHT_HOUR=$CONFIG_VALUE ;;
        NIGHT_TZ) NIGHT_TZ=$CONFIG_VALUE ;;
    esac
done <<CONFIG_DATA
$CONFIG_VALUES
CONFIG_DATA
unset CONFIG_VALUES CONFIG_KEY CONFIG_VALUE
AWGM="$AWGM_URL"
CLASH="$CLASH_URL"

CACHE="/opt/var/run/vpn-delay.cache"
LASTFILE="/opt/var/run/vpn-delay.last"
LOG="/opt/var/log/vpn-watchdog.log"

LOCK="/tmp/vpn-delay-scan.lock"


INTERVAL=86400
RETRY_AFTER=3600
CONCURRENCY=4
PROGRESS_STEP=40
MODE="${1:-normal}"

mkdir -p /opt/var/run /opt/var/log

NOW=$(date +%s)
LAST=0

[ -f "$LASTFILE" ] && LAST=$(cat "$LASTFILE" 2>/dev/null)
[ -n "$LAST" ] || LAST=0

AGE=$((NOW - LAST))

# Обычно полный scan нужен только раз в 24 часа.
# Ночной optimizer вызывает --force после refresh подписки.
if [ "$MODE" != "--force" ]; then
    [ "$AGE" -lt "$INTERVAL" ] && exit 0
fi

mkdir "$LOCK" 2>/dev/null || exit 0
WORK=$(mktemp -d /tmp/vpn-delay-scan.XXXXXX) || {
    rmdir "$LOCK"
    exit 1
}

trap 'rm -rf "$LOCK" "$WORK"' EXIT INT TERM

log()
{
    LINE="$(date '+%Y-%m-%d %H:%M:%S') $*"
    echo "$LINE" >> "$LOG"
    [ "$MODE" = "--force" ] && echo "$LINE"
}

SELECTOR_JSON=$(curl -s "$CLASH/proxies/$SELECTOR")

MEMBERS=$(printf '%s\n' "$SELECTOR_JSON" |
    sed -n 's/.*"all":[[:space:]]*\[\([^]]*\)\].*/\1/p' |
    tr ',' '\n' |
    sed 's/[[:space:]"]//g')

if [ -z "$MEMBERS" ]; then
    log "DELAY-SCAN ERROR: cannot read selector members"
    echo $((NOW - INTERVAL + RETRY_AFTER)) > "$LASTFILE"
    exit 1
fi

TOTAL_EXPECTED=$(printf '%s\n' "$MEMBERS" | awk 'NF {n++} END {print n+0}')
log "DELAY-SCAN: starting $TOTAL_EXPECTED servers, concurrency=$CONCURRENCY"

probe_one()
{
    TAG="$1"
    OUT="$2"

    RESPONSE=$(curl \
        --connect-timeout 3 \
        --max-time 15 \
        -sS \
        -X POST \
        "$AWGM/api/singbox/tunnels/delay-check?tag=$TAG" \
        2>/dev/null)

    DELAY=$(printf '%s\n' "$RESPONSE" |
        sed -n 's/.*"delay":[[:space:]]*\([0-9][0-9]*\).*/\1/p')

    [ -n "$DELAY" ] || DELAY=0

    echo "$TAG $DELAY $(date +%s)" > "$OUT"
}

N=0
BATCH=0
NEXT_PROGRESS=$PROGRESS_STEP

for TAG in $MEMBERS
do
    [ -z "$TAG" ] && continue

    N=$((N + 1))
    BATCH=$((BATCH + 1))

    probe_one "$TAG" "$WORK/$N" &

    if [ "$BATCH" -ge "$CONCURRENCY" ]; then
        wait
        BATCH=0

        if [ "$N" -ge "$NEXT_PROGRESS" ]; then
            log "DELAY-SCAN: progress $N/$TOTAL_EXPECTED"
            while [ "$NEXT_PROGRESS" -le "$N" ]
            do
                NEXT_PROGRESS=$((NEXT_PROGRESS + PROGRESS_STEP))
            done
        fi
    fi
done

wait

TMP="$CACHE.tmp.$$"
: > "$TMP"

for F in "$WORK"/*
do
    [ -f "$F" ] && cat "$F" >> "$TMP"
done

TOTAL=$(wc -l < "$TMP")
OK=$(awk '$2 > 0 {n++} END {print n+0}' "$TMP")

if [ "$TOTAL" -eq 0 ] || [ "$OK" -eq 0 ]; then
    rm -f "$TMP"

    # Старый cache оставляем. Следующая попытка через час.
    echo $((NOW - INTERVAL + RETRY_AFTER)) > "$LASTFILE"

    log "DELAY-SCAN ERROR: no usable results"
    exit 1
fi

mv "$TMP" "$CACHE"
echo "$NOW" > "$LASTFILE"

log "DELAY-SCAN: measured $OK/$TOTAL servers"

exit 0
