#!/bin/sh
export PATH="${PATH:+$PATH:}/opt/bin:/opt/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
# VPN Optimizer nightly optimizer v2.5.1

# Настройки роутера хранятся только в локальном конфигурационном файле.
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
SPEED_URL="https://speed.cloudflare.com/__down?bytes=5242880"

LOG="/opt/var/log/vpn-watchdog.log"
BADFILE="/opt/var/run/vpn-watchdog.bad"
DELAYFILE="/opt/var/run/vpn-delay.cache"
DEEP_LAST="/opt/var/run/vpn-deep.last"
SPEED_MONITOR_LAST="/opt/var/run/vpn-speed-monitor.last"
SPEED_MONITOR_STATE="/opt/var/run/vpn-speed-monitor.state"

STATE_DIR="/opt/var/lib/vpn-optimizer"
SPEEDCACHE="$STATE_DIR/speed-cache.tsv"
SPEEDMETA="$STATE_DIR/speed-cache.meta"
SPEEDFAIL="$STATE_DIR/speed-failures.tsv"
NIGHT_LAST="$STATE_DIR/nightly-last-date"
NIGHT_RETRY="$STATE_DIR/nightly-retry"
DAYSTATE="$STATE_DIR/server-day-state.tsv"

OPTLOCK="/tmp/vpn-optimize.lock"
WDLOCK="/tmp/vpn-watchdog.lock"
RETRY_AFTER=600
COOLDOWN=1800

INITIAL_BEST=6
MIN_GOOD=3
MAX_SPEED_TESTS=18
MAX_CANDIDATE_ATTEMPTS=30

GOOD_MBPS=20
RESERVE_MBPS=8

ROUTING_OFF=0
ORIGINAL_MODE=""
HAVE_WDLOCK=0
ORIGIN=""
MODE="${1:-normal}"

case "$MODE" in
    --recheck)
        LABEL="RECHECK"
        RUN_KIND="recheck"
        ;;
    --force)
        LABEL="NIGHTLY"
        RUN_KIND="force"
        ;;
    *)
        MODE="normal"
        LABEL="NIGHTLY"
        RUN_KIND="nightly"
        ;;
esac

mkdir -p /opt/var/log /opt/var/run "$STATE_DIR"
touch "$DAYSTATE"

if ! mkdir "$OPTLOCK" 2>/dev/null; then
    [ "$MODE" = "normal" ] && exit 0
    echo "VPN Optimizer optimizer is already running" >&2
    exit 2
fi

WORK_DIR=$(mktemp -d /tmp/vpn-nightly.XXXXXX) || {
    rmdir "$OPTLOCK"
    exit 1
}
CAND_ALL="$WORK_DIR/candidates.all"
CAND_PLAN="$WORK_DIR/candidates.plan"
CACHE_TMP="$WORK_DIR/speed-cache.tsv"
FAIL_TMP="$WORK_DIR/speed-failures.tsv"
SWITCH_OUT="$WORK_DIR/switch.out"
: > "$CACHE_TMP"
: > "$FAIL_TMP"

log()
{
    LINE="$(date '+%Y-%m-%d %H:%M:%S') $*"
    echo "$LINE" >> "$LOG"

    case "$MODE" in
        --force|--recheck) echo "$LINE" ;;
    esac
}

mark_retry()
{
    [ "$MODE" = "normal" ] && echo "$(date +%s)" > "$NIGHT_RETRY"
}

reset_speed_monitor()
{
    SERVER="$1"
    NOW_MON=$(date +%s)
    echo "$SERVER $NOW_MON" > "$SPEED_MONITOR_LAST"
    echo "NORMAL $SERVER" > "$SPEED_MONITOR_STATE"
}

notify_nightly()
{
    [ "$MODE" = "--recheck" ] && return 0
    [ -x "$BOT" ] || return 0

    if ! "$BOT" --notify-nightly >> "$LOG" 2>&1; then
        log "$LABEL WARNING: Telegram report failed"
        return 1
    fi

    log "$LABEL: Telegram report sent"
    return 0
}

notify_error()
{
    # /recheck вызывается из Telegram-бота: ошибку вернёт сам обработчик команды,
    # поэтому отдельное широковещательное сообщение здесь не отправляем.
    [ "$MODE" = "--recheck" ] && return 0
    [ -x "$BOT" ] || return 0

    if ! "$BOT" --notify-nightly-error "$*" >> "$LOG" 2>&1; then
        log "$LABEL WARNING: Telegram error notification failed"
        return 1
    fi

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
    [ -f "$DELAYFILE" ] || {
        echo 0
        return
    }

    D=$(awk -v s="$1" '$1==s {d=$2} END {print d}' "$DELAYFILE")

    if [ -n "$D" ] && [ "$D" -gt 0 ] 2>/dev/null; then
        echo "$D"
    else
        echo 0
    fi
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


delay_for_log()
{
    VALUE=$(printf '%s' "$1" | sed 's/^0*//')
    [ -n "$VALUE" ] || VALUE=0
    echo "$VALUE"
}

health_quick()
{
    CODE=$(curl -x "$PROXY" \
        --connect-timeout 5 \
        --max-time 10 \
        -sS \
        -o "$WORK_DIR/quick.out" \
        -w '%{http_code}' \
        "$CHECK_URL" 2>/dev/null) || return 1

    [ "$CODE" = "200" ] || return 1
    grep -q 'gateway.discord.gg' "$WORK_DIR/quick.out"
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

mark_deep_ok()
{
    echo "$1 $(date +%s)" > "$DEEP_LAST"
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

day_reset_selected()
{
    TAG="$1"
    NOW_STATE=$(date +%s)
    printf '%s	USED	1	0	%s	nightly
' "$TAG" "$NOW_STATE" > "$DAYSTATE"
}

cached_speed_class()
{
    [ -s "$SPEEDCACHE" ] || { echo ""; return; }
    awk -v s="$1" '$1==s {v=$4} END {print v}' "$SPEEDCACHE"
}

recheck_candidate_priority()
{
    TAG="$1"
    STATE=$(day_state "$TAG")
    CLASS=$(cached_speed_class "$TAG")

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

get_router_mode()
{
    JSON=$(curl -sS --max-time 10 \
        "$AWGM/api/singbox/router/settings" 2>/dev/null) || return 1

    ENABLED=$(printf '%s\n' "$JSON" |
        sed -n 's/.*"enabled":[[:space:]]*\(true\|false\).*/\1/p' |
        head -n 1)

    MODE=$(printf '%s\n' "$JSON" |
        sed -n 's/.*"routingMode":[[:space:]]*"\([^"]*\)".*/\1/p' |
        head -n 1)

    if [ "$ENABLED" = "false" ]; then
        echo off
        return 0
    fi

    [ "$ENABLED" = "true" ] || return 1
    [ -n "$MODE" ] || return 1
    echo "$MODE"
}

set_router_mode()
{
    TARGET="$1"

    HTTP=$(curl -sS \
        --max-time 15 \
        -o "$WORK_DIR/router-mode.out" \
        -w '%{http_code}' \
        -X POST \
        -H 'Content-Type: application/json' \
        -d "{\"mode\":\"$TARGET\"}" \
        "$AWGM/api/singbox/router/mode" \
        2>/dev/null)

    [ "$HTTP" = "200" ] || return 1

    I=0
    while [ "$I" -lt 45 ]
    do
        CURRENT_MODE=$(get_router_mode 2>/dev/null)
        [ "$CURRENT_MODE" = "$TARGET" ] && return 0

        sleep 1
        I=$((I + 1))
    done

    return 1
}

restore_routing()
{
    [ "$ROUTING_OFF" = "1" ] || return 0
    [ -n "$ORIGINAL_MODE" ] || return 1

    log "$LABEL: restoring router mode $ORIGINAL_MODE"

    if set_router_mode "$ORIGINAL_MODE"; then
        ROUTING_OFF=0
        log "$LABEL: router mode restored to $ORIGINAL_MODE"
        return 0
    fi

    log "$LABEL ERROR: cannot restore router mode $ORIGINAL_MODE"
    return 1
}

cleanup()
{
    if [ "$ROUTING_OFF" = "1" ]; then
        restore_routing >/dev/null 2>&1
    fi

    [ "$HAVE_WDLOCK" = "1" ] && rm -rf "$WDLOCK"
    rm -rf "$OPTLOCK" "$WORK_DIR"
}

trap cleanup EXIT INT TERM

speed_test()
{
    RESULT=$(curl -x "$PROXY" \
        --connect-timeout 5 \
        --max-time 10 \
        -sS \
        -o /dev/null \
        -w '%{http_code} %{size_download} %{speed_download}' \
        "$SPEED_URL" 2>/dev/null)

    CODE=${RESULT%% *}
    REST=${RESULT#* }
    SIZE=${REST%% *}
    BPS=${REST#* }

    [ -n "$BPS" ] || BPS=0
    [ -n "$SIZE" ] || SIZE=0

    MBPS=$(awk -v s="$BPS" 'BEGIN {printf "%.1f", (s * 8) / 1000000}')

    # Даже если curl завершился по max-time, speed_download остаётся полезным:
    # сервер с хорошей скоростью успевает скачать 5 MiB намного раньше 10 секунд.
    echo "$MBPS"
}

classify_speed()
{
    SPEED="$1"

    awk -v s="$SPEED" -v g="$GOOD_MBPS" -v r="$RESERVE_MBPS" 'BEGIN {
        if (s >= g) print "GOOD";
        else if (s >= r) print "RESERVE";
        else print "SLOW";
    }'
}

write_failure()
{
    TAG="$1"
    DELAY="$2"
    REASON="$3"
    printf '%s\t%s\t%s\t%s\n' \
        "$TAG" "$DELAY" "$REASON" "$(date +%s)" >> "$FAIL_TMP"
}

append_speed_result()
{
    TAG="$1"
    DELAY="$2"
    SPEED="$3"
    CLASS="$4"

    printf '%s\t%s\t%s\t%s\t%s\t0\n' \
        "$TAG" "$DELAY" "$SPEED" "$CLASS" "$(date +%s)" >> "$CACHE_TMP"
}

select_best_from_cache()
{
    BEST=$(awk '
        $4 == "GOOD" {
            d=$2+0;
            if (d <= 0) d=999999;
            if (!found || d < bestd) {
                found=1; bestd=d; tag=$1;
            }
        }
        END { if (found) print tag }
    ' "$CACHE_TMP")

    if [ -n "$BEST" ]; then
        echo "$BEST"
        return
    fi

    awk '
        {
            s=$3+0;
            if (!found || s > bests) {
                found=1; bests=s; tag=$1;
            }
        }
        END { if (found) print tag }
    ' "$CACHE_TMP"
}


select_best_recheck()
{
    # Anti-ping-pong + качество: untried GOOD/RESERVE идут раньше уже
    # использованных, а любой DEGRADED остаётся последним резервом.
    # Внутри GOOD выбираем меньший delay; внутри RESERVE/SLOW — большую speed.
    awk 'FILENAME==ARGV[1] {st[$1]=$2; next} {
        state=st[$1]; if (state=="") state="UNTRIED";
        d=$2+0; if (d<=0) d=999999;
        speed=$3+0;
        class=$4;

        if (state=="UNTRIED" && class=="GOOD") rank=1;
        else if (state=="UNTRIED" && class=="RESERVE") rank=2;
        else if (state=="USED" && class=="GOOD") rank=3;
        else if (state=="USED" && class=="RESERVE") rank=4;
        else if (state=="UNTRIED" && class=="SLOW") rank=5;
        else if (state=="USED" && class=="SLOW") rank=6;
        else if (state=="DEGRADED" && class=="GOOD") rank=7;
        else if (state=="DEGRADED" && class=="RESERVE") rank=8;
        else rank=9;

        metric=(class=="GOOD" ? d : -speed);
        if (!found || rank < brank || (rank==brank && metric < bmetric)) {
            found=1; brank=rank; bmetric=metric; tag=$1;
        }
    } END {if (found) print tag}' "$DAYSTATE" "$CACHE_TMP"
}

mark_selected_in_cache()
{
    SELECTED="$1"
    OUT="$CACHE_TMP.selected"

    awk -v s="$SELECTED" 'BEGIN {OFS="\t"} {
        $6=($1==s ? 1 : 0);
        print $1,$2,$3,$4,$5,$6;
    }' "$CACHE_TMP" > "$OUT"

    mv "$OUT" "$CACHE_TMP"
}

count_class()
{
    awk -v c="$1" '$4==c {n++} END {print n+0}' "$CACHE_TMP"
}

final_health()
{
    if health_quick && health_deep; then
        mark_deep_ok "$1"
        return 0
    fi

    sleep 3

    if health_quick && health_deep; then
        mark_deep_ok "$1"
        return 0
    fi

    return 1
}

night_date()
{
    if [ "$NIGHT_TZ" = "LOCAL" ]; then
        date "$@"
    else
        TZ="$NIGHT_TZ" date "$@"
    fi
}

TODAY=$(night_date '+%Y-%m-%d')
HOUR=$(night_date '+%H')
NOW=$(date +%s)

if [ "$MODE" = "normal" ]; then
    [ "$HOUR" = "$NIGHT_HOUR" ] || exit 0

    if [ -f "$NIGHT_LAST" ]; then
        LAST_DATE= LAST_EPOCH= LAST_TZ=
        read -r LAST_DATE LAST_EPOCH LAST_TZ < "$NIGHT_LAST"
        # The old date-only file remains readable. A future date also blocks
        # repeats after a clock/calendar rollback.
        if [ "$LAST_DATE" = "$TODAY" ] || [ "$LAST_DATE" \> "$TODAY" ]; then
            exit 0
        fi
        # Only when NIGHT_TZ changed: avoid another successful cycle within
        # 24 hours. No extra file and no change to normal daily retries.
        case "$LAST_EPOCH" in
            ''|*[!0-9]*) ;;
            *)
                if [ "${#LAST_EPOCH}" -le 12 ] && [ -n "$LAST_TZ" ] &&
                   [ "$LAST_TZ" != "$NIGHT_TZ" ] && [ "$((NOW - LAST_EPOCH))" -lt 86400 ]; then
                    exit 0
                fi
                ;;
        esac
    fi

    if [ -f "$NIGHT_RETRY" ]; then
        RETRY=$(cat "$NIGHT_RETRY" 2>/dev/null)
        [ -n "$RETRY" ] || RETRY=0
        RETRY_AGE=$((NOW - RETRY))
        [ "$RETRY_AGE" -lt "$RETRY_AFTER" ] && exit 0
    fi
fi

WAITED=0
while ! mkdir "$WDLOCK" 2>/dev/null
do
    sleep 2
    WAITED=$((WAITED + 2))

    if [ "$WAITED" -ge 60 ]; then
        log "$LABEL: watchdog busy; retry later"
        mark_retry
        [ "$MODE" = "--recheck" ] && exit 2
        exit 0
    fi
done
HAVE_WDLOCK=1

ORIGIN=$(get_current)
if [ -z "$ORIGIN" ]; then
    log "$LABEL ERROR: cannot determine current server"
    mark_retry
    notify_error "не удалось определить текущий сервер"
    exit 1
fi

if [ "$MODE" = "--recheck" ]; then
    log "$LABEL: starting speed benchmark from existing delay-cache; origin=$ORIGIN"

    if [ ! -s "$DELAYFILE" ]; then
        log "$LABEL ERROR: delay-cache is missing"
        notify_error "delay-cache отсутствует; сначала нужен ночной цикл или --force"
        exit 1
    fi

    REFRESH_STATUS="skipped"
else
    log "$LABEL: starting controlled refresh and benchmark; origin=$ORIGIN; schedule=$NIGHT_HOUR:00 $NIGHT_TZ"

    HTTP=$(curl -sS \
        --max-time 180 \
        -o "$WORK_DIR/refresh.out" \
        -w '%{http_code}' \
        -X POST \
        "$AWGM/api/singbox/subscriptions/refresh?id=$SUB_ID" \
        2>/dev/null)

    if [ "$HTTP" != "200" ]; then
        log "$LABEL ERROR: subscription refresh failed HTTP=$HTTP"
        mark_retry
        notify_error "refresh подписки завершился ошибкой HTTP=$HTTP"
        exit 1
    fi

    log "$LABEL: subscription refresh completed"
    sleep 5

    /opt/bin/vpn-delay-scan.sh --force
    if [ "$?" != "0" ]; then
        log "$LABEL ERROR: delay scan failed"
        mark_retry
        notify_error "delay-scan завершился ошибкой"
        exit 1
    fi

    REFRESH_STATUS="ok"
fi

SELECTOR_JSON=$(get_selector)
MEMBERS=$(printf '%s\n' "$SELECTOR_JSON" |
    sed -n 's/.*"all":[[:space:]]*\[\([^]]*\)\].*/\1/p' |
    tr ',' '\n' |
    sed 's/[[:space:]"]//g')

if [ -z "$MEMBERS" ]; then
    log "$LABEL ERROR: cannot read selector members"
    mark_retry
    notify_error "не удалось прочитать список серверов подписки"
    exit 1
fi

: > "$CAND_ALL"
INDEX=0

for SERVER in $MEMBERS
do
    [ -z "$SERVER" ] && continue

    TYPE=$(get_type "$SERVER")
    [ "$TYPE" = "Trojan" ] && continue
    is_bad "$SERVER" && continue

    DELAY=$(get_delay "$SERVER")
    [ "$DELAY" -gt 0 ] 2>/dev/null || continue

    INDEX=$((INDEX + 1))
    PRIORITY=0
    [ "$MODE" = "--recheck" ] && PRIORITY=$(recheck_candidate_priority "$SERVER")

    printf '%03d %09d %05d %s %s\n' \
        "$PRIORITY" "$DELAY" "$INDEX" "$SERVER" "$TYPE" >> "$CAND_ALL"
done

sort "$CAND_ALL" -o "$CAND_ALL"

if [ ! -s "$CAND_ALL" ]; then
    log "$LABEL ERROR: no measurable non-Trojan candidates"
    mark_retry
    notify_error "после delay-scan нет подходящих кандидатов"
    exit 1
fi

head -n "$INITIAL_BEST" "$CAND_ALL" > "$CAND_PLAN"
INITIAL_TARGET=$(wc -l < "$CAND_PLAN")

if ! awk -v s="$ORIGIN" '$4==s {found=1} END {exit !found}' "$CAND_PLAN"; then
    TYPE=$(get_type "$ORIGIN")

    if [ "$TYPE" != "Trojan" ] && ! is_bad "$ORIGIN"; then
        DELAY=$(get_delay "$ORIGIN")
        [ "$DELAY" -gt 0 ] 2>/dev/null || DELAY=999999
        INDEX=$((INDEX + 1))
        PRIORITY=0
        [ "$MODE" = "--recheck" ] && PRIORITY=$(recheck_candidate_priority "$ORIGIN")
        printf '%03d %09d %05d %s %s\n' \
            "$PRIORITY" "$DELAY" "$INDEX" "$ORIGIN" "$TYPE" >> "$CAND_PLAN"
        INITIAL_TARGET=$((INITIAL_TARGET + 1))
    fi
fi

awk 'NR==FNR {seen[$4]=1; next} !seen[$4] {print}' \
    "$CAND_PLAN" "$CAND_ALL" >> "$CAND_PLAN.more"
cat "$CAND_PLAN.more" >> "$CAND_PLAN"

ORIGINAL_MODE=$(get_router_mode)
if [ -z "$ORIGINAL_MODE" ]; then
    log "$LABEL ERROR: cannot determine sing-box router mode"
    mark_retry
    notify_error "не удалось определить режим маршрутизации AWGM"
    exit 1
fi

if [ "$ORIGINAL_MODE" != "off" ]; then
    log "$LABEL: switching router mode $ORIGINAL_MODE -> off"

    # Ответ API может быть потерян после фактического переключения.
    # Даже при ошибке пытаемся вернуть исходную маршрутизацию через cleanup.
    ROUTING_OFF=1
    if ! set_router_mode off; then
        log "$LABEL ERROR: cannot switch router mode to off"
        mark_retry
        notify_error "не удалось временно отключить VPN-маршрутизацию"
        exit 1
    fi
fi

ATTEMPTS=0
SPEED_TESTED=0
GOOD_COUNT=0

while read PRIORITY DELAY INDEX SERVER TYPE
do
    [ -z "$SERVER" ] && continue

    DELAY_NUM=$(delay_for_log "$DELAY")

    ATTEMPTS=$((ATTEMPTS + 1))
    [ "$ATTEMPTS" -gt "$MAX_CANDIDATE_ATTEMPTS" ] && break
    [ "$SPEED_TESTED" -ge "$MAX_SPEED_TESTS" ] && break

    CURRENT=$(get_current)

    if [ "$CURRENT" != "$SERVER" ]; then
        HTTP=$(switch_to "$SERVER")

        if [ "$HTTP" != "200" ]; then
            log "$LABEL BAD: cannot select $SERVER ($TYPE), HTTP=$HTTP"
            write_failure "$SERVER" "$DELAY_NUM" "switch"
            [ "$MODE" = "--recheck" ] && day_mark_degraded "$SERVER" "recheck-switch"
            continue
        fi

        sleep 2
    fi

    if ! health_quick; then
        log "$LABEL BAD: $SERVER ($TYPE, ${DELAY_NUM}ms, quick-check)"
        write_failure "$SERVER" "$DELAY_NUM" "quick"
        mark_bad "$SERVER"
        [ "$MODE" = "--recheck" ] && day_mark_degraded "$SERVER" "recheck-quick"
        continue
    fi

    if ! health_deep; then
        log "$LABEL BAD: $SERVER ($TYPE, ${DELAY_NUM}ms, deep-check)"
        write_failure "$SERVER" "$DELAY_NUM" "deep"
        mark_bad "$SERVER"
        [ "$MODE" = "--recheck" ] && day_mark_degraded "$SERVER" "recheck-deep"
        continue
    fi

    mark_deep_ok "$SERVER"

    SPEED=$(speed_test)
    CLASS=$(classify_speed "$SPEED")
    SPEED_TESTED=$((SPEED_TESTED + 1))

    [ "$CLASS" = "GOOD" ] && GOOD_COUNT=$((GOOD_COUNT + 1))

    append_speed_result "$SERVER" "$DELAY_NUM" "$SPEED" "$CLASS"
    log "$LABEL SPEED: $SERVER ($TYPE, ${DELAY_NUM}ms, ${SPEED}Mbit/s, $CLASS)"

    if [ "$SPEED_TESTED" -ge "$INITIAL_TARGET" ] && \
       [ "$GOOD_COUNT" -ge "$MIN_GOOD" ]; then
        break
    fi

done < "$CAND_PLAN"

if [ ! -s "$CACHE_TMP" ]; then
    log "$LABEL ERROR: no server completed speed benchmark"

    if [ -n "$ORIGIN" ]; then
        switch_to "$ORIGIN" >/dev/null 2>&1
    fi

    restore_routing
    mark_retry
    notify_error "ни один сервер не прошёл ночной benchmark"
    exit 1
fi

if [ "$MODE" = "--recheck" ]; then
    ORIGIN_CLASS=$(awk -v s="$ORIGIN" '$1==s {print $4}' "$CACHE_TMP")

    ORIGIN_DAY_STATE=$(day_state "$ORIGIN")

    if [ "$ORIGIN_CLASS" = "GOOD" ] && [ "$ORIGIN_DAY_STATE" != "DEGRADED" ]; then
        BEST="$ORIGIN"
        log "$LABEL: current server is still GOOD; keeping $ORIGIN"
    else
        BEST=$(select_best_recheck)
    fi
else
    BEST=$(select_best_from_cache)
fi

if [ -z "$BEST" ]; then
    log "$LABEL ERROR: cannot select best benchmarked server"
    restore_routing
    mark_retry
    notify_error "не удалось выбрать сервер из speed-cache"
    exit 1
fi

HTTP=$(switch_to "$BEST")
if [ "$HTTP" != "200" ]; then
    log "$LABEL ERROR: cannot select best server $BEST, HTTP=$HTTP"
    restore_routing
    mark_retry
    notify_error "не удалось включить выбранный сервер"
    exit 1
fi

sleep 2
mark_selected_in_cache "$BEST"

if ! restore_routing; then
    mark_retry
    notify_error "benchmark завершён, но не удалось восстановить VPN-маршрутизацию"
    exit 1
fi

sleep 3

if ! final_health "$BEST"; then
    log "$LABEL ERROR: selected $BEST failed final health after routing restore"
    mark_bad "$BEST"
    [ "$MODE" = "--recheck" ] && day_mark_degraded "$BEST" "recheck-final-health"

    # Пробуем остальные уже измеренные серверы: GOOD -> RESERVE -> SLOW,
    # внутри класса по минимальному delay.
    awk '
        {
            p=3;
            if ($4=="GOOD") p=1;
            else if ($4=="RESERVE") p=2;
            d=$2+0; if (d<=0) d=999999;
            printf "%02d %09d %s\n", p, d, $1;
        }
    ' "$CACHE_TMP" | sort > "$WORK_DIR/final-fallback"

    FINAL=""

    while read PRIORITY DELAY SERVER
    do
        [ "$SERVER" = "$BEST" ] && continue
        is_bad "$SERVER" && continue

        HTTP=$(switch_to "$SERVER")
        [ "$HTTP" = "200" ] || continue
        sleep 2

        if final_health "$SERVER"; then
            FINAL="$SERVER"
            break
        fi

        mark_bad "$SERVER"
        [ "$MODE" = "--recheck" ] && day_mark_degraded "$SERVER" "recheck-final-health"
    done < "$WORK_DIR/final-fallback"

    if [ -z "$FINAL" ]; then
        log "$LABEL ERROR: all benchmarked servers failed final health"
        mark_retry
        notify_error "после восстановления маршрутизации все измеренные серверы провалили контроль"
        exit 1
    fi

    BEST="$FINAL"
    mark_selected_in_cache "$BEST"
fi

reset_speed_monitor "$BEST"

if [ "$MODE" = "--recheck" ]; then
    if [ "$BEST" != "$ORIGIN" ]; then
        day_mark_degraded "$ORIGIN" "speed-recheck"
        day_mark_used "$BEST" "recheck"
    fi
else
    # Успешный полный benchmark открывает новый дневной цикл anti-ping-pong.
    day_reset_selected "$BEST"
fi

# Счётчики считаем ДО переноса временного cache в постоянный файл.
GOOD_COUNT=$(count_class GOOD)
RESERVE_COUNT=$(count_class RESERVE)
SLOW_COUNT=$(count_class SLOW)

mv "$CACHE_TMP" "$SPEEDCACHE"
mv "$FAIL_TMP" "$SPEEDFAIL"

DELAY_TOTAL=$(wc -l < "$DELAYFILE" 2>/dev/null)
[ -n "$DELAY_TOTAL" ] || DELAY_TOTAL=0
DELAY_OK=$(awk '$2 > 0 {n++} END {print n+0}' "$DELAYFILE" 2>/dev/null)
[ -n "$DELAY_OK" ] || DELAY_OK=0
FINISHED_LOCAL="$(night_date '+%d.%m.%Y %H:%M %z') ($NIGHT_TZ)"

cat > "$SPEEDMETA.tmp" <<META
version=2.5.1
run_kind=$RUN_KIND
date=$TODAY
finished_local=$FINISHED_LOCAL
epoch=$(date +%s)
delay_ok=$DELAY_OK
delay_total=$DELAY_TOTAL
subscription_total=$DELAY_TOTAL
refresh=$REFRESH_STATUS
speed_tested=$SPEED_TESTED
good=$GOOD_COUNT
reserve=$RESERVE_COUNT
slow=$SLOW_COUNT
selected=$BEST
router_mode=$ORIGINAL_MODE
router_restored=ok
META
mv "$SPEEDMETA.tmp" "$SPEEDMETA"

if [ "$MODE" = "normal" ]; then
    printf '%s %s %s\n' "$TODAY" "$(date +%s)" "$NIGHT_TZ" > "$NIGHT_LAST"
    rm -f "$NIGHT_RETRY"
fi

FINAL_DELAY=$(get_delay "$BEST")
FINAL_SPEED=$(awk -v s="$BEST" '$1==s {print $3}' "$SPEEDCACHE")
FINAL_CLASS=$(awk -v s="$BEST" '$1==s {print $4}' "$SPEEDCACHE")

log "$LABEL OK: selected $BEST (${FINAL_DELAY}ms, ${FINAL_SPEED}Mbit/s, $FINAL_CLASS); tested=$SPEED_TESTED good=$GOOD_COUNT reserve=$RESERVE_COUNT slow=$SLOW_COUNT"

if [ "$MODE" != "--recheck" ]; then
    notify_nightly
fi

exit 0
