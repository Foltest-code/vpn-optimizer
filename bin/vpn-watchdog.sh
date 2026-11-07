#!/bin/sh
# VPN Optimizer watchdog v2.5.1

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
PROXY="$PROXY_URL"

BOT="/opt/etc/telegram-vpn-bot/bot.py"

CHECK_URL="https://discord.com/api/v10/gateway"

DEEP_URL="https://speed.cloudflare.com/__down?bytes=65536"
DEEP_BYTES=65536
DEEP_INTERVAL=600
DEEP_LAST="/opt/var/run/vpn-deep.last"

SPEED_URL="https://speed.cloudflare.com/__down?bytes=5242880"
SPEED_MONITOR_INTERVAL=3600
SPEED_MONITOR_THRESHOLD=5
SPEED_MONITOR_RETRY=60
SPEED_MONITOR_LAST="/opt/var/run/vpn-speed-monitor.last"
SPEED_MONITOR_STATE="/opt/var/run/vpn-speed-monitor.state"
GOOD_MBPS=20
RESERVE_MBPS=8

LOG="/opt/var/log/vpn-watchdog.log"
BADFILE="/opt/var/run/vpn-watchdog.bad"
DELAYFILE="/opt/var/run/vpn-delay.cache"
STATE_DIR="/opt/var/lib/vpn-optimizer"
SPEEDCACHE="$STATE_DIR/speed-cache.tsv"
DAYSTATE="$STATE_DIR/server-day-state.tsv"
SPEED_MAX_AGE=172800
# Fresh speed test is mandatory for failover. Cached speeds rank candidates only.
MAX_SPEED_TESTS_FAILOVER=18

LOCK="/tmp/vpn-watchdog.lock"


COOLDOWN=1800
MAX_ATTEMPTS=25

MODE="${1:-normal}"

mkdir -p /opt/var/log /opt/var/run "$STATE_DIR"
touch "$DAYSTATE"

case "$MODE" in
    --reselect|--speed-current|--switch-cached)
        WAITED=0

        while ! mkdir "$LOCK" 2>/dev/null
        do
            sleep 2
            WAITED=$((WAITED + 2))

            if [ "$WAITED" -ge 60 ]; then
                exit 1
            fi
        done
        ;;
    *)
        mkdir "$LOCK" 2>/dev/null || exit 0
        ;;
esac

WORK_DIR=$(mktemp -d /tmp/vpn-watchdog.XXXXXX) || {
    rmdir "$LOCK"
    exit 1
}
TMP="$WORK_DIR/quick.out"
CAND="$WORK_DIR/candidates"
SWITCH_OUT="$WORK_DIR/switch.out"
trap 'rm -rf "$LOCK" "$WORK_DIR"' EXIT INT TERM

log()
{
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

notify_switch()
{
    OLD="$1"
    NEW="$2"
    REASON="$3"
    DELAY="$4"
    TYPE="$5"

    [ -x "$BOT" ] || return 0

    if ! "$BOT" --notify-switch \
        "$OLD" "$NEW" "$REASON" "$DELAY" "$TYPE" \
        >> "$LOG" 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Telegram switch notification failed" >> "$LOG"
    fi
}

notify_speed_low()
{
    SERVER="$1"
    SPEED1="$2"
    SPEED2="$3"
    DELAY="$4"

    [ -x "$BOT" ] || return 0

    if ! "$BOT" --notify-speed-low \
        "$SERVER" "$SPEED1" "$SPEED2" "$DELAY" \
        >> "$LOG" 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Telegram low-speed notification failed" >> "$LOG"
    fi
}

notify_speed_restored()
{
    SERVER="$1"
    SPEED="$2"
    DELAY="$3"

    [ -x "$BOT" ] || return 0

    if ! "$BOT" --notify-speed-restored \
        "$SERVER" "$SPEED" "$DELAY" \
        >> "$LOG" 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING: Telegram speed-recovery notification failed" >> "$LOG"
    fi
}

delay_for_log()
{
    VALUE=$(printf '%s' "$1" | sed 's/^0*//')
    [ -n "$VALUE" ] || VALUE=0
    echo "$VALUE"
}

speed_test()
{
    RESULT=$(curl -x "$PROXY" \
        --connect-timeout 5 \
        --max-time 12 \
        -sS \
        -o /dev/null \
        -w '%{http_code} %{size_download} %{speed_download}' \
        "$SPEED_URL" 2>/dev/null) || { echo 0.0; return; }

    CODE=${RESULT%% *}
    REST=${RESULT#* }
    BYTES=${REST%% *}
    BPS=${REST#* }
    if [ "$CODE" != "200" ] || ! [ "$BYTES" -ge 65536 ] 2>/dev/null; then
        echo 0.0
        return
    fi
    awk -v s="$BPS" 'BEGIN {printf "%.1f", (s * 8) / 1000000}'
}

classify_speed()
{
    awk -v s="$1" -v g="$GOOD_MBPS" -v r="$RESERVE_MBPS" 'BEGIN {
        if (s >= g) print "GOOD";
        else if (s >= r) print "RESERVE";
        else print "SLOW";
    }'
}

median3()
{
    awk -v a="$1" -v b="$2" -v c="$3" 'BEGIN {
        if (a > b) { t=a; a=b; b=t }
        if (b > c) { t=b; b=c; c=t }
        if (a > b) { t=a; a=b; b=t }
        printf "%.1f", b
    }'
}

day_state()
{
    TAG="$1"
    STATE=$(awk -v s="$TAG" '$1==s {v=$2} END {print v}' "$DAYSTATE" 2>/dev/null)
    [ -n "$STATE" ] && echo "$STATE" || echo "UNTRIED"
}

day_mark_used()
{
    TAG="$1"
    REASON="$2"
    NOW_STATE=$(date +%s)

    OLD_STATE=$(day_state "$TAG")
    SELECTS=$(awk -v s="$TAG" '$1==s {v=$3} END {print v+0}' "$DAYSTATE" 2>/dev/null)
    PROBLEMS=$(awk -v s="$TAG" '$1==s {v=$4} END {print v+0}' "$DAYSTATE" 2>/dev/null)
    SELECTS=$((SELECTS + 1))

    # Если сервер уже деградировал сегодня, сам факт повторного выбора
    # не снимает DEGRADED до следующего успешного ночного benchmark.
    NEW_STATE="USED"
    [ "$OLD_STATE" = "DEGRADED" ] && NEW_STATE="DEGRADED"

    awk -v s="$TAG" '$1!=s' "$DAYSTATE" 2>/dev/null > "$DAYSTATE.tmp"
    printf '%s	%s	%s	%s	%s	%s
' \
        "$TAG" "$NEW_STATE" "$SELECTS" "$PROBLEMS" "$NOW_STATE" "$REASON" >> "$DAYSTATE.tmp"
    mv "$DAYSTATE.tmp" "$DAYSTATE"
}

day_mark_degraded()
{
    TAG="$1"
    REASON="$2"
    NOW_STATE=$(date +%s)

    SELECTS=$(awk -v s="$TAG" '$1==s {v=$3} END {print v+0}' "$DAYSTATE" 2>/dev/null)
    PROBLEMS=$(awk -v s="$TAG" '$1==s {v=$4} END {print v+0}' "$DAYSTATE" 2>/dev/null)
    PROBLEMS=$((PROBLEMS + 1))

    awk -v s="$TAG" '$1!=s' "$DAYSTATE" 2>/dev/null > "$DAYSTATE.tmp"
    printf '%s	DEGRADED	%s	%s	%s	%s
' \
        "$TAG" "$SELECTS" "$PROBLEMS" "$NOW_STATE" "$REASON" >> "$DAYSTATE.tmp"
    mv "$DAYSTATE.tmp" "$DAYSTATE"
}

candidate_priority()
{
    STATE="$1"
    CLASS="$2"

    case "$STATE:$CLASS" in
        UNTRIED:GOOD) echo 10 ;;
        UNTRIED:RESERVE) echo 20 ;;
        USED:GOOD) echo 30 ;;
        USED:RESERVE) echo 40 ;;
        UNTRIED:"") echo 50 ;;
        USED:"") echo 60 ;;
        UNTRIED:SLOW) echo 70 ;;
        USED:SLOW) echo 80 ;;
        DEGRADED:GOOD) echo 90 ;;
        DEGRADED:RESERVE) echo 100 ;;
        DEGRADED:"") echo 110 ;;
        DEGRADED:SLOW) echo 120 ;;
        *) echo 60 ;;
    esac
}

speed_is_low()
{
    awk -v s="$1" -v t="$SPEED_MONITOR_THRESHOLD" 'BEGIN {exit !(s < t)}'
}

reset_speed_monitor()
{
    SERVER="$1"
    NOW=$(date +%s)
    echo "$SERVER $NOW" > "$SPEED_MONITOR_LAST"
    echo "NORMAL $SERVER" > "$SPEED_MONITOR_STATE"
}

speed_monitor_due()
{
    SERVER="$1"

    if [ ! -f "$SPEED_MONITOR_LAST" ]; then
        reset_speed_monitor "$SERVER"
        return 1
    fi

    LAST_SERVER=$(awk '{print $1}' "$SPEED_MONITOR_LAST")
    LAST_TS=$(awk '{print $2}' "$SPEED_MONITOR_LAST")
    [ -n "$LAST_TS" ] || LAST_TS=0

    if [ "$LAST_SERVER" != "$SERVER" ]; then
        reset_speed_monitor "$SERVER"
        return 1
    fi

    NOW=$(date +%s)
    AGE=$((NOW - LAST_TS))
    [ "$AGE" -ge "$SPEED_MONITOR_INTERVAL" ]
}

speed_monitor_state()
{
    SERVER="$1"
    [ -f "$SPEED_MONITOR_STATE" ] || { echo NORMAL; return; }

    STATE=$(awk '{print $1}' "$SPEED_MONITOR_STATE")
    STATE_SERVER=$(awk '{print $2}' "$SPEED_MONITOR_STATE")

    if [ "$STATE_SERVER" != "$SERVER" ]; then
        echo NORMAL
    else
        echo "${STATE:-NORMAL}"
    fi
}

set_speed_monitor_state()
{
    echo "$1 $2" > "$SPEED_MONITOR_STATE"
}

monitor_current_speed()
{
    SERVER="$1"
    speed_monitor_due "$SERVER" || return 0

    DELAY=$(get_delay "$SERVER")
    DELAY_PRINT=$(delay_for_log "$DELAY")
    [ "$DELAY" -lt 999999 ] 2>/dev/null || DELAY_PRINT="unknown"

    PREVIOUS_STATE=$(speed_monitor_state "$SERVER")
    SPEED1=$(speed_test)
    log "SPEED-MONITOR: $SERVER ${SPEED1}Mbit/s (threshold ${SPEED_MONITOR_THRESHOLD}Mbit/s)"

    if ! speed_is_low "$SPEED1"; then
        if [ "$PREVIOUS_STATE" = "SLOW" ]; then
            notify_speed_restored "$SERVER" "$SPEED1" "$DELAY_PRINT"
            log "SPEED-MONITOR: $SERVER recovered to ${SPEED1}Mbit/s"
        fi

        set_speed_monitor_state NORMAL "$SERVER"
        echo "$SERVER $(date +%s)" > "$SPEED_MONITOR_LAST"
        return 0
    fi

    log "SPEED-MONITOR: $SERVER first low result ${SPEED1}Mbit/s; retry in ${SPEED_MONITOR_RETRY}s"
    sleep "$SPEED_MONITOR_RETRY"

    CURRENT_AFTER=$(get_current)
    if [ "$CURRENT_AFTER" != "$SERVER" ]; then
        [ -n "$CURRENT_AFTER" ] && reset_speed_monitor "$CURRENT_AFTER"
        return 0
    fi

    SPEED2=$(speed_test)
    log "SPEED-MONITOR: $SERVER retry ${SPEED2}Mbit/s"

    if speed_is_low "$SPEED2"; then
        if [ "$PREVIOUS_STATE" != "SLOW" ]; then
            notify_speed_low "$SERVER" "$SPEED1" "$SPEED2" "$DELAY_PRINT"
            log "SPEED-MONITOR ALERT: $SERVER ${SPEED1}/${SPEED2}Mbit/s"
        fi

        set_speed_monitor_state SLOW "$SERVER"
        echo "$SERVER $(date +%s)" > "$SPEED_MONITOR_LAST"
        return 3
    else
        if [ "$PREVIOUS_STATE" = "SLOW" ]; then
            notify_speed_restored "$SERVER" "$SPEED2" "$DELAY_PRINT"
            log "SPEED-MONITOR: $SERVER recovered to ${SPEED2}Mbit/s"
        fi

        set_speed_monitor_state NORMAL "$SERVER"
    fi

    echo "$SERVER $(date +%s)" > "$SPEED_MONITOR_LAST"
    return 0
}

get_selector()
{
    curl -s "$CLASH/proxies/$SELECTOR"
}

get_current()
{
    get_selector |
        sed -n 's/.*"now":[[:space:]]*"\([^"]*\)".*/\1/p'
}

get_type()
{
    curl -s "$CLASH/proxies/$1" |
        sed -n 's/.*"type":[[:space:]]*"\([^"]*\)".*/\1/p'
}

get_delay()
{
    if [ ! -f "$DELAYFILE" ]; then
        echo 999999
        return
    fi

    D=$(awk -v s="$1" '$1==s {d=$2} END {print d}' "$DELAYFILE")

    if [ -n "$D" ] && [ "$D" -gt 0 ] 2>/dev/null; then
        echo "$D"
    else
        echo 999999
    fi
}


speed_cache_fresh()
{
    [ -s "$SPEEDCACHE" ] || return 1

    TS=$(awk '$5 > max {max=$5} END {print max+0}' "$SPEEDCACHE")
    [ "$TS" -gt 0 ] 2>/dev/null || return 1

    NOW=$(date +%s)
    AGE=$((NOW - TS))

    [ "$AGE" -ge 0 ] && [ "$AGE" -le "$SPEED_MAX_AGE" ]
}

speed_class()
{
    [ -s "$SPEEDCACHE" ] || {
        echo ""
        return
    }

    awk -v s="$1" '$1==s {c=$4} END {print c}' "$SPEEDCACHE"
}

health_quick()
{
    CODE=$(curl -x "$PROXY" \
        --connect-timeout 5 \
        --max-time 10 \
        -sS \
        -o "$TMP" \
        -w '%{http_code}' \
        "$CHECK_URL" 2>/dev/null) || return 1

    [ "$CODE" = "200" ] || return 1
    grep -q 'gateway.discord.gg' "$TMP"
}

health_deep()
{
    RESULT=$(curl -x "$PROXY" \
        --connect-timeout 5 \
        --max-time 15 \
        -sS \
        -o /dev/null \
        -w '%{http_code} %{size_download}' \
        "$DEEP_URL" 2>/dev/null) || return 1

    CODE=${RESULT%% *}
    SIZE=${RESULT#* }
    SIZE=${SIZE%%.*}

    [ "$CODE" = "200" ] || return 1
    [ -n "$SIZE" ] || return 1
    [ "$SIZE" -ge "$DEEP_BYTES" ] 2>/dev/null
}

deep_due()
{
    SERVER="$1"

    [ -f "$DEEP_LAST" ] || return 0

    LAST_SERVER=$(awk '{print $1}' "$DEEP_LAST")
    LAST_TS=$(awk '{print $2}' "$DEEP_LAST")

    [ "$LAST_SERVER" = "$SERVER" ] || return 0
    [ -n "$LAST_TS" ] || return 0

    NOW=$(date +%s)
    AGE=$((NOW - LAST_TS))

    [ "$AGE" -lt 0 ] && return 0
    [ "$AGE" -ge "$DEEP_INTERVAL" ]
}

mark_deep_ok()
{
    echo "$1 $(date +%s)" > "$DEEP_LAST"
}

switch_to()
{
    : > "$SWITCH_OUT"

    curl -sS \
        --max-time 30 \
        -o "$SWITCH_OUT" \
        -w '%{http_code}' \
        -X POST \
        -H 'Content-Type: application/json' \
        -d "{\"memberTag\":\"$1\"}" \
        "$AWGM/api/singbox/subscriptions/active-member?id=$SUB_ID" \
        2>/dev/null
}


mark_bad()
{
    NOW=$(date +%s)

    grep -v "^$1 " "$BADFILE" 2>/dev/null > "$BADFILE.tmp"
    echo "$1 $NOW" >> "$BADFILE.tmp"
    mv "$BADFILE.tmp" "$BADFILE"
}

is_bad()
{
    [ -f "$BADFILE" ] || return 1

    NOW=$(date +%s)
    TS=$(awk -v s="$1" '$1==s {print $2}' "$BADFILE")

    [ -n "$TS" ] || return 1

    AGE=$((NOW - TS))
    [ "$AGE" -lt "$COOLDOWN" ]
}

check_current()
{
    #
    # QUICK:
    # две неудачи с интервалом 10 секунд.
    #
    if ! health_quick; then
        sleep 10

        if ! health_quick; then
            return 1
        fi
    fi

    # DEEP: периодический 64 КиБ тест отключён в v2.5.
    # Аварийная функция health_deep() остаётся доступной при failover.
    # if deep_due "$CURRENT"; then
    #     if health_deep; then
    #         mark_deep_ok "$CURRENT"
    #         return 0
    #     fi
    #     sleep 5
    #     if health_deep; then
    #         mark_deep_ok "$CURRENT"
    #         return 0
    #     fi
    #     return 2
    # fi

    return 0
}

CURRENT=$(get_current)

if [ -z "$CURRENT" ]; then
    log "ERROR: cannot determine current server"
    exit 1
fi

if [ "$MODE" = "--speed-current" ]; then
    SPEED1=$(speed_test)
    sleep 1
    [ "$(get_current)" = "$CURRENT" ] || {
        echo "SPEED_CURRENT_ERROR|server-changed"
        exit 2
    }

    SPEED2=$(speed_test)
    sleep 1
    [ "$(get_current)" = "$CURRENT" ] || {
        echo "SPEED_CURRENT_ERROR|server-changed"
        exit 2
    }

    SPEED3=$(speed_test)
    MEDIAN=$(median3 "$SPEED1" "$SPEED2" "$SPEED3")
    CLASS=$(classify_speed "$MEDIAN")
    DELAY=$(get_delay "$CURRENT")
    [ "$DELAY" -lt 999999 ] 2>/dev/null || DELAY=0
    printf 'SPEED_CURRENT|%s|%s|%s|%s|%s|%s|%s\n' \
        "$CURRENT" "$DELAY" "$SPEED1" "$SPEED2" "$SPEED3" "$MEDIAN" "$CLASS"
    exit 0
fi

# /switch: только готовый speed-cache, никаких новых speed/delay тестов.
if [ "$MODE" = "--switch-cached" ]; then
    if ! speed_cache_fresh; then
        echo "SWITCH_CACHED_ERROR|missing-or-expired-cache"
        exit 2
    fi

    SELECTOR_JSON=$(get_selector)
    MEMBERS=$(printf '%s\n' "$SELECTOR_JSON" |
        sed -n 's/.*"all":[[:space:]]*\[\([^]]*\)\].*/\1/p' |
        tr ',' '\n' | sed 's/[[:space:]"]//g')
    if [ -z "$MEMBERS" ]; then
        echo "SWITCH_CACHED_ERROR|missing-members"
        exit 2
    fi

    # Свежесть проверяется для КАЖДОЙ записи, а не только всего файла.
    NOW=$(date +%s)
    awk -v now="$NOW" -v age="$SPEED_MAX_AGE" -v current="$CURRENT" '
        NF>=6 && $1!=current && ($4=="GOOD" || $4=="RESERVE" || $4=="SLOW") &&
        $5>0 && now>=$5 && now-$5<=age && $3>=0 {
            d=$2+0; if(d<=0) d=999999;
            rank=($4=="GOOD" ? 1 : ($4=="RESERVE" ? 2 : 3));
            metric=($4=="GOOD" ? d : -($3+0));
            printf "%d %.3f %s %s %s\n", rank, metric, $1, $3, $4;
        }
    ' "$SPEEDCACHE" | sort -k1,1n -k2,2n -k3,3 > "$CAND.sorted"

    if [ ! -s "$CAND.sorted" ]; then
        echo "SWITCH_CACHED_ERROR|no-alternatives"
        exit 2
    fi

    # Сохранить кэш без текущего сервера заранее, но НЕ записывать до успеха.
    CACHE_NEW="$SPEEDCACHE.tmp.$$"
    ROLLBACK_NEEDED=0
    switch_cached_cleanup()
    {
        # Попытка отката также при SIGINT/SIGTERM или неожиданной ошибке.
        if [ "$ROLLBACK_NEEDED" = "1" ] && [ "$(get_current)" != "$CURRENT" ]; then
            switch_to "$CURRENT" >/dev/null
            if [ "$(get_current)" != "$CURRENT" ]; then
                log "SWITCH-CACHED ERROR: cleanup could not restore original server"
            fi
        fi
        rm -f "$CACHE_NEW"
        rm -rf "$LOCK" "$WORK_DIR"
    }
    trap 'switch_cached_cleanup' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    ATTEMPTS=0
    while read PRIORITY METRIC SERVER CACHED_SPEED CACHED_CLASS
    do
        [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ] && break
        printf '%s\n' "$MEMBERS" | grep -Fxq "$SERVER" || continue
        [ "$(get_type "$SERVER")" = "Trojan" ] && continue
        is_bad "$SERVER" && continue
        ATTEMPTS=$((ATTEMPTS + 1))

        ROLLBACK_NEEDED=1
        HTTP=$(switch_to "$SERVER")
        if [ "$HTTP" != "200" ]; then
            log "SWITCH-CACHED: cannot select $SERVER (HTTP=$HTTP)"
            continue
        fi
        sleep 2
        if [ "$(get_current)" != "$SERVER" ] || ! health_quick; then
            log "SWITCH-CACHED: $SERVER failed quick HTTPS verification"
            continue
        fi

        if ! awk -v old="$CURRENT" -v chosen="$SERVER" 'BEGIN {OFS="\t"}
            $1!=old {$6=($1==chosen ? 1 : 0); print $1,$2,$3,$4,$5,$6}
        ' "$SPEEDCACHE" > "$CACHE_NEW" || ! mv "$CACHE_NEW" "$SPEEDCACHE"; then
            log "SWITCH-CACHED: cannot update speed-cache; restoring original"
            break
        fi

        ROLLBACK_NEEDED=0
        log "SWITCH-CACHED OK: $CURRENT -> $SERVER (cached ${CACHED_SPEED}Mbit/s, $CACHED_CLASS)"
        printf 'SWITCH_CACHED_OK|%s|%s|%s|%s\n' "$CURRENT" "$SERVER" "$CACHED_SPEED" "$CACHED_CLASS"
        exit 0
    done < "$CAND.sorted"

    # Даже если API ошибся, он мог фактически применить смену сервера.
    if [ "$ROLLBACK_NEEDED" = "1" ] && [ "$(get_current)" != "$CURRENT" ]; then
        HTTP=$(switch_to "$CURRENT")
        if [ "$(get_current)" != "$CURRENT" ]; then
            log "SWITCH-CACHED ERROR: failed to restore $CURRENT (HTTP=$HTTP)"
            echo "SWITCH_CACHED_ERROR|rollback-failed"
            exit 3
        fi
    fi
    ROLLBACK_NEEDED=0
    log "SWITCH-CACHED: no confirmed alternative; original=$CURRENT"
    echo "SWITCH_CACHED_ERROR|no-working-alternative"
    exit 2
fi

FAIL_REASON=""

if [ "$MODE" != "--reselect" ]; then
    check_current
    STATUS=$?

    if [ "$STATUS" -eq 0 ]; then
        monitor_current_speed "$CURRENT"
        MONITOR_STATUS=$?
        if [ "$MONITOR_STATUS" -ne 3 ]; then
            exit 0
        fi
        FAIL_REASON="speed-low"
        log "SPEED-FAILOVER: confirmed low speed; looking for a better server"
        # Current tunnel still works. Only accept a measured faster replacement.
        SPEED_FAILOVER=1
        # Do not mark working server as unavailable.
    fi

    if [ "$STATUS" -eq 0 ]; then
        :
    elif [ "$STATUS" -eq 1 ]; then
        FAIL_REASON="quick-check"
        log "FAIL: $CURRENT quick-check failed twice; starting failover"
    else
        FAIL_REASON="deep-check"
        log "FAIL: $CURRENT deep-check failed twice; starting failover"
    fi

    if [ "$STATUS" -ne 0 ]; then
        mark_bad "$CURRENT"
        day_mark_degraded "$CURRENT" "$FAIL_REASON"
    fi
else
    log "RESELECT: starting sorted selection from current $CURRENT"
fi

SELECTOR_JSON=$(get_selector)

MEMBERS=$(printf '%s\n' "$SELECTOR_JSON" |
    sed -n 's/.*"all":[[:space:]]*\[\([^]]*\)\].*/\1/p' |
    tr ',' '\n' |
    sed 's/[[:space:]"]//g')

INDEX=0
: > "$CAND"

CACHE_FRESH=0
if speed_cache_fresh; then
    CACHE_FRESH=1
fi

for SERVER in $MEMBERS
do
    [ -z "$SERVER" ] && continue

    if [ "$MODE" != "--reselect" ] && [ "$SERVER" = "$CURRENT" ]; then
        continue
    fi

    TYPE=$(get_type "$SERVER")

    [ "$TYPE" = "Trojan" ] && continue
    is_bad "$SERVER" && continue

    INDEX=$((INDEX + 1))
    DELAY=$(get_delay "$SERVER")
    CLASS=""

    if [ "$CACHE_FRESH" = "1" ]; then
        CLASS=$(speed_class "$SERVER")
    fi

    DAY_STATE=$(day_state "$SERVER")
    PRIORITY=$(candidate_priority "$DAY_STATE" "$CLASS")

    printf '%03d %09d %05d %s %s\n' \
        "$PRIORITY" "$DELAY" "$INDEX" "$SERVER" "$TYPE" >> "$CAND"
done

sort "$CAND" > "$CAND.sorted"

ATTEMPTS=0
SPEED_TESTED=0
SPEED_FAILOVER=${SPEED_FAILOVER:-0}
# Keep only a tag and its measured speed. Reserve selection happens after the scan.
BEST_GOOD=""
BEST_GOOD_SPEED=0
BEST_RESERVE=""
BEST_RESERVE_SPEED=0
BEST_SLOW=""
BEST_SLOW_SPEED=0

while read PRIORITY DELAY INDEX SERVER TYPE
do
    [ -z "$SERVER" ] && continue
    [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ] && break
    [ "$SPEED_TESTED" -ge "$MAX_SPEED_TESTS_FAILOVER" ] && break
    ATTEMPTS=$((ATTEMPTS + 1))

    HTTP=$(switch_to "$SERVER")
    if [ "$HTTP" != "200" ]; then
        log "ERROR: cannot select $SERVER ($TYPE), HTTP=$HTTP"
        day_mark_degraded "$SERVER" "switch-error"
        continue
    fi
    sleep 2

    if ! health_quick; then
        log "BAD: $SERVER ($TYPE, quick-check)"
        mark_bad "$SERVER"
        day_mark_degraded "$SERVER" "quick-check"
        continue
    fi
    if ! health_deep; then
        log "BAD: $SERVER ($TYPE, deep-check)"
        mark_bad "$SERVER"
        day_mark_degraded "$SERVER" "deep-check"
        continue
    fi

    SPEED=$(speed_test)
    SPEED_TESTED=$((SPEED_TESTED + 1))
    CLASS=$(classify_speed "$SPEED")
    log "CANDIDATE SPEED: $SERVER ($TYPE, ${SPEED}Mbit/s, $CLASS)"

    if [ "$SPEED_FAILOVER" = "1" ]; then
        # 8 Mbit/s minimum; do not replace a merely slow but functioning
        # server with another measured slow server.
        [ "$CLASS" = "SLOW" ] && continue
    fi

    case "$CLASS" in
        GOOD)
            if awk -v a="$SPEED" -v b="$BEST_GOOD_SPEED" 'BEGIN {exit !(a > b)}'; then
                BEST_GOOD="$SERVER"; BEST_GOOD_SPEED="$SPEED"
            fi
            ;;
        RESERVE)
            if awk -v a="$SPEED" -v b="$BEST_RESERVE_SPEED" 'BEGIN {exit !(a > b)}'; then
                BEST_RESERVE="$SERVER"; BEST_RESERVE_SPEED="$SPEED"
            fi
            ;;
        SLOW)
            if awk -v a="$SPEED" -v b="$BEST_SLOW_SPEED" 'BEGIN {exit !(a > b)}'; then
                BEST_SLOW="$SERVER"; BEST_SLOW_SPEED="$SPEED"
            fi
            ;;
    esac
    # Good is already fully checked at current time. Restore quickly.
    if [ "$CLASS" = "GOOD" ]; then
        break
    fi
done < "$CAND.sorted"

CHOSEN=""
CHOSEN_SPEED=0
if [ -n "$BEST_GOOD" ]; then
    CHOSEN="$BEST_GOOD"; CHOSEN_SPEED="$BEST_GOOD_SPEED"
elif [ -n "$BEST_RESERVE" ]; then
    CHOSEN="$BEST_RESERVE"; CHOSEN_SPEED="$BEST_RESERVE_SPEED"
elif [ "$SPEED_FAILOVER" != "1" ] && [ -n "$BEST_SLOW" ]; then
    CHOSEN="$BEST_SLOW"; CHOSEN_SPEED="$BEST_SLOW_SPEED"
fi

if [ -n "$CHOSEN" ]; then
    HTTP=$(switch_to "$CHOSEN")
    if [ "$HTTP" = "200" ]; then
        sleep 2
        if health_quick && health_deep; then
            mark_deep_ok "$CHOSEN"
            reset_speed_monitor "$CHOSEN"
            day_mark_used "$CHOSEN" "${FAIL_REASON:-reselect}"
            if [ "$MODE" = "--reselect" ]; then
                log "RESELECT OK: selected $CHOSEN (${CHOSEN_SPEED}Mbit/s, fresh-speed)"
            else
                log "OK: switched $CURRENT -> $CHOSEN (${CHOSEN_SPEED}Mbit/s, fresh-speed, reason=$FAIL_REASON)"
                notify_switch "$CURRENT" "$CHOSEN" "$FAIL_REASON" "$(delay_for_log "$(get_delay "$CHOSEN")")" "$(get_type "$CHOSEN")"
            fi
            exit 0
        fi
        log "ERROR: selected candidate $CHOSEN failed final health check"
    else
        log "ERROR: cannot select final candidate $CHOSEN, HTTP=$HTTP"
    fi
fi

HTTP=$(switch_to "$CURRENT")

if [ "$HTTP" = "200" ]; then
    log "ERROR: no suitable server found after $ATTEMPTS attempts ($SPEED_TESTED speed tests); restored $CURRENT"
else
    log "ERROR: no suitable server found after $ATTEMPTS attempts; restore failed HTTP=$HTTP"
fi

exit 1
