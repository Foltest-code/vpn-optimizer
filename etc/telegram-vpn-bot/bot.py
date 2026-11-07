#!/opt/bin/python3
# VPN Optimizer Telegram bot v2.5.1

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE_DIR = "/opt/etc/telegram-vpn-bot"

OFFSET_FILE = BASE_DIR + "/offset"
CONFIG_FILE = "/opt/etc/vpn-optimizer.conf"


sys.path.insert(0, "/opt/lib/vpn-optimizer")
from common import load_config, validate_ruleset

try:
    CONFIG = load_config(CONFIG_FILE)
except ValueError as error:
    print("VPN Optimizer: " + str(error), file=sys.stderr)
    sys.exit(1)
RULESET_TAG = CONFIG["RULESET_TAG"]
AWGM_BASE = CONFIG["AWGM_URL"]
AWG_BASE = AWGM_BASE + "/api/singbox/router"
TG_PROXY = CONFIG["PROXY_URL"]
SUB_ID = CONFIG["SUB_ID"]
WATCHDOG_SCRIPT = "/opt/bin/vpn-watchdog.sh"
OPTIMIZER_SCRIPT = "/opt/bin/vpn-optimize.sh"

SPEED_DIR = "/opt/var/lib/vpn-optimizer"
SPEED_CACHE = SPEED_DIR + "/speed-cache.tsv"
SPEED_META = SPEED_DIR + "/speed-cache.meta"
SPEED_FAILURES = SPEED_DIR + "/speed-failures.tsv"


TOKEN = CONFIG["TG_BOT_TOKEN"]
try:
    ALLOWED_USERS = {int(x.strip()) for x in CONFIG["TG_ALLOWED_IDS"].split(",")}
    if not ALLOWED_USERS or any(user <= 0 for user in ALLOWED_USERS):
        raise ValueError()
except ValueError:
    raise ValueError("Invalid TG_ALLOWED_IDS in VPN Optimizer config") from None


tg_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({
        "http": TG_PROXY,
        "https": TG_PROXY,
    })
)


def telegram(method, data=None, timeout=65):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    body = None
    headers = {}

    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers
    )

    try:
        with tg_opener.open(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        # HTTPError может содержать полный URL с секретным bot token.
        raise RuntimeError(f"Telegram transport error ({type(error).__name__})") from None

    if not result.get("ok"):
        code = result.get("error_code")
        raise RuntimeError(f"Telegram API error code={code if type(code) is int else 'unknown'}")

    return result["result"]


def send_message(chat_id, text, parse_mode=None):
    data = {
        "chat_id": str(chat_id),
        "text": text,
    }

    if parse_mode:
        data["parse_mode"] = parse_mode

    telegram(
        "sendMessage",
        data,
        timeout=15,
    )


def notify_allowed_users(text):
    ok = True

    for chat_id in sorted(ALLOWED_USERS):
        try:
            send_message(chat_id, text)
        except Exception as error:
            ok = False
            print(
                f"NOTIFY ERROR ({type(error).__name__})",
                flush=True,
            )

    return ok


def awg_request(path, payload=None):
    url = AWG_BASE + path

    if payload is None:
        request = urllib.request.Request(url, method="GET")
    else:
        body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"AWG transport error ({type(error).__name__})") from None

    if not isinstance(result, dict) or not result.get("success"):
        raise RuntimeError("AWG API request failed; response omitted")

    return result.get("data")


def awgm_request(path, payload=None, timeout=180):
    url = AWGM_BASE + path

    if payload is None:
        request = urllib.request.Request(url, method="GET")
    else:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"AWGM transport error ({type(error).__name__})") from None

    if not isinstance(result, dict) or not result.get("success"):
        raise RuntimeError("AWGM API request failed; response omitted")

    return result.get("data")


def find_subscription(value):
    if isinstance(value, dict):
        if value.get("id") == SUB_ID:
            return value

        for item in value.values():
            found = find_subscription(item)
            if found is not None:
                return found

    elif isinstance(value, list):
        for item in value:
            found = find_subscription(item)
            if found is not None:
                return found

    return None


def get_subscription():
    data = awgm_request("/api/singbox/subscriptions", timeout=30)
    sub = find_subscription(data)

    if sub is None:
        raise RuntimeError("настроенная VPN-подписка не найдена")

    return sub


def get_active_member():
    query = urllib.parse.quote(SUB_ID, safe="")

    try:
        data = awgm_request(
            f"/api/singbox/subscriptions/active-now?id={query}",
            timeout=15,
        )

        if isinstance(data, dict):
            tag = (data.get("now") or "").strip()

            if tag:
                return tag
    except Exception:
        pass

    return (get_subscription().get("activeMember") or "").strip()


def member_map(subscription):
    result = {}

    for key in ("members", "excludedMembers", "filteredMembers"):
        for member in subscription.get(key) or []:
            if not isinstance(member, dict):
                continue

            tag = member.get("tag")

            if tag:
                result[tag] = member

    return result


def protocol_name(value):
    value = (value or "").lower()

    names = {
        "vless": "VLESS",
        "hysteria2": "Hysteria2",
        "shadowsocks": "Shadowsocks",
        "trojan": "Trojan",
    }

    return names.get(value, value or "")


def format_member(member, fallback_tag=""):
    if not isinstance(member, dict):
        return fallback_tag or "неизвестный сервер"

    label = (member.get("label") or "").strip()
    tag = (member.get("tag") or fallback_tag or "").strip()
    protocol = protocol_name(member.get("protocol"))

    name = label or tag or "неизвестный сервер"

    if protocol:
        return f"{name} / {protocol}"

    return name


def describe_tag(tag, subscription=None):
    if not tag:
        return "неизвестный сервер"

    if subscription is None:
        subscription = get_subscription()

    member = member_map(subscription).get(tag)

    if member is None:
        return tag

    return format_member(member, tag)


def get_excluded_entries(subscription=None):
    if subscription is None:
        subscription = get_subscription()

    tags = list(subscription.get("excludedTags") or [])
    members = member_map(subscription)

    return [
        (tag, members.get(tag, {"tag": tag}))
        for tag in tags
    ]


def subscription_action(path, member_tags):
    query = urllib.parse.quote(SUB_ID, safe="")

    return awgm_request(
        f"/api/singbox/subscriptions/members/{path}?id={query}",
        {"memberTags": list(member_tags)},
        timeout=180,
    )


def run_reselect():
    result = subprocess.run(
        [WATCHDOG_SCRIPT, "--reselect"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )

    output = result.stdout.decode("utf-8", errors="replace").strip()

    return result.returncode, output


def run_speed_current():
    result = subprocess.run(
        [WATCHDOG_SCRIPT, "--speed-current"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    )

    output = result.stdout.decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        detail = output.splitlines()[-1] if output else "неизвестная ошибка"
        raise RuntimeError(f"speed-test завершился с ошибкой: {detail}")

    line = next(
        (x for x in reversed(output.splitlines()) if x.startswith("SPEED_CURRENT|")),
        None,
    )

    if not line:
        raise RuntimeError("speed-test не вернул результат")

    parts = line.split("|", 7)

    if len(parts) != 8:
        raise RuntimeError("некорректный результат speed-test")

    _, tag, delay, speed1, speed2, speed3, median, speed_class = parts

    try:
        delay_value = int(delay)
    except Exception:
        delay_value = 0

    samples = []
    for value in (speed1, speed2, speed3):
        try:
            samples.append(float(value))
        except Exception:
            samples.append(0.0)

    try:
        median_value = float(median)
    except Exception:
        median_value = 0.0

    return {
        "tag": tag,
        "delay": delay_value,
        "samples": samples,
        "speed": median_value,
        "class": speed_class,
    }


def run_speed_recheck():
    result = subprocess.run(
        [OPTIMIZER_SCRIPT, "--recheck"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )

    output = result.stdout.decode("utf-8", errors="replace").strip()

    return result.returncode, output


def run_switch_cached():
    result = subprocess.run(
        [WATCHDOG_SCRIPT, "--switch-cached"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    output = result.stdout.decode("utf-8", errors="replace").strip()
    line = next((x for x in reversed(output.splitlines())
                 if x.startswith("SWITCH_CACHED_")), "")
    return result.returncode, line


def handle_switch_cached(chat_id):
    send_message(chat_id, "Пробую переключиться на лучший альтернативный VPN Optimizer-сервер из сохранённой статистики.")
    code, line = run_switch_cached()
    if code != 0:
        reasons = {
            "missing-or-expired-cache": "Нет свежей статистики скорости (до 48 часов). Запусти /recheck.",
            "missing-members": "Не удалось получить список серверов подписки.",
            "no-alternatives": "В сохранённой статистике нет подходящих альтернатив.",
            "no-working-alternative": "Подтверждённой альтернативы нет. Исходный сервер восстановлен.",
            "rollback-failed": "Не удалось подтвердить возврат исходного сервера. Требуется ручная проверка в AWGM.",
        }
        reason = line.split("|", 1)[1] if "|" in line else ""
        send_message(chat_id, "VPN Optimizer — /switch: " + reasons.get(reason, "Переключение завершилось с ошибкой. Проверь AWGM."))
        return
    parts = line.split("|", 4)
    if len(parts) != 5 or parts[0] != "SWITCH_CACHED_OK":
        send_message(chat_id, "VPN Optimizer — /switch: неожиданный ответ watchdog. Проверь AWGM.")
        return
    _, old_tag, new_tag, speed, speed_class = parts
    try:
        old_name = describe_tag(old_tag)
    except Exception:
        old_name = old_tag
    try:
        new_name = describe_tag(new_tag)
    except Exception:
        new_name = new_tag
    send_message(
        chat_id,
        "VPN Optimizer — /switch выполнен\n\n"
        f"Было: {old_name}\n"
        f"Стало: {new_name}\n"
        f"Сохранённая скорость: {speed} Mbit/s ({speed_class})\n"
        "HTTPS-проверка пройдена. Предыдущий сервер удалён из speed-cache.",
    )


def get_domains():
    return validate_ruleset(awg_request("/rulesets/list"), RULESET_TAG)


def set_domains(domains, expected=None):
    current = get_domains()  # Always validate immediately before writing.
    if expected is not None and current != expected:
        raise ValueError("VPN-список изменился; повтори команду")
    domains = sorted(set(x.lower() for x in domains))

    rules = []

    if domains:
        rules.append({
            "domain_suffix": domains
        })

    payload = {
        "tag": RULESET_TAG,
        "ruleSet": {
            "tag": RULESET_TAG,
            "type": "inline",
            "rules": rules,
        }
    }

    awg_request("/rulesets/update", payload)


def normalize_domain(value):
    value = value.strip()

    if not value:
        raise ValueError("пустой адрес")

    if "://" not in value:
        parsed = urllib.parse.urlparse("//" + value)
    else:
        parsed = urllib.parse.urlparse(value)

    host = parsed.hostname

    if not host:
        raise ValueError("не удалось определить домен")

    host = host.lower().rstrip(".")

    if host.startswith("www."):
        host = host[4:]

    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        raise ValueError("некорректное имя домена")

    domain_re = re.compile(
        r"^(?=.{1,253}$)"
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
    )

    if not domain_re.match(host):
        raise ValueError("некорректный домен")

    return host



def read_key_values(path):
    result = {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")

                if not line or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                result[key] = value
    except FileNotFoundError:
        pass

    return result


def read_speed_cache():
    rows = []

    try:
        with open(SPEED_CACHE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")

                if len(parts) < 6:
                    continue

                tag, delay, speed, speed_class, measured, selected = parts[:6]

                try:
                    delay_value = int(delay)
                except Exception:
                    delay_value = 0

                try:
                    speed_value = float(speed)
                except Exception:
                    speed_value = 0.0

                try:
                    measured_value = int(measured)
                except Exception:
                    measured_value = 0

                rows.append({
                    "tag": tag,
                    "delay": delay_value,
                    "speed": speed_value,
                    "class": speed_class,
                    "measured": measured_value,
                    "selected": selected == "1",
                })
    except FileNotFoundError:
        pass

    return rows


def clean_table_name(value):
    value = value or ""
    value = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", value)
    value = re.sub(r"[\U0001F300-\U0001FAFF]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def short_member_name(member, tag):
    label = clean_table_name((member or {}).get("label") or "")
    protocol = protocol_name((member or {}).get("protocol"))

    name = label or tag

    if protocol:
        if protocol.lower() not in name.lower():
            name = f"{name} {protocol}"

    return name


def speedcache_report_html(title="VPN Optimizer — speed-cache"):
    rows = read_speed_cache()

    if not rows:
        return None

    meta = read_key_values(SPEED_META)

    class_order = {
        "GOOD": 1,
        "RESERVE": 2,
        "SLOW": 3,
    }

    rows.sort(
        key=lambda row: (
            class_order.get(row["class"], 9),
            row["delay"] if row["delay"] > 0 else 999999,
            -row["speed"],
        )
    )

    try:
        sub = get_subscription()
        members = member_map(sub)
    except Exception:
        members = {}

    lines = []
    lines.append("#  Server                 ms  Mbit/s  Class")

    for index, row in enumerate(rows, start=1):
        member = members.get(row["tag"], {"tag": row["tag"]})
        name = short_member_name(member, row["tag"])

        if len(name) > 21:
            name = name[:20] + "…"

        delay = str(row["delay"]) if row["delay"] > 0 else "-"
        selected = " *" if row["selected"] else ""

        lines.append(
            f"{index:>2} {name:<21} {delay:>4} "
            f"{row['speed']:>7.1f}  {row['class']:<7}{selected}"
        )

    measured_at = meta.get("finished_local") or meta.get("date") or "неизвестно"
    delay_ok = meta.get("delay_ok", "?")
    delay_total = meta.get("delay_total", "?")
    tested = meta.get("speed_tested", str(len(rows)))
    good = meta.get("good", "?")
    reserve = meta.get("reserve", "?")
    slow = meta.get("slow", "?")

    selected_row = next((row for row in rows if row["selected"]), None)

    subscription_total = meta.get("subscription_total", delay_total)
    refresh = meta.get("refresh", "?")
    router_restored = meta.get("router_restored", "?")
    router_mode = meta.get("router_mode", "?")

    run_kind = meta.get("run_kind", "nightly")

    if refresh == "ok":
        refresh_text = "обновлена"
    elif refresh == "skipped" and run_kind == "recheck":
        refresh_text = "без обновления (recheck)"
    else:
        refresh_text = refresh

    routing_text = (
        f"{router_mode} восстановлен"
        if router_restored == "ok"
        else str(router_restored)
    )

    summary = [
        f"<b>{html.escape(title)}</b>",
        html.escape(str(measured_at)),
        "",
        f"Подписка: {html.escape(str(refresh_text))} · "
        f"серверов: {html.escape(str(subscription_total))}",
        f"Delay: {html.escape(str(delay_ok))}/{html.escape(str(delay_total))} · "
        f"Speed: {html.escape(str(tested))}",
        f"GOOD: {html.escape(str(good))} · "
        f"RESERVE: {html.escape(str(reserve))} · "
        f"SLOW: {html.escape(str(slow))}",
        f"Маршрутизация: {html.escape(routing_text)}",
        "",
        "<pre>" + html.escape("\n".join(lines)) + "</pre>",
        "* выбранный сервер",
    ]

    if selected_row:
        member = members.get(
            selected_row["tag"],
            {"tag": selected_row["tag"]},
        )
        selected_name = format_member(member, selected_row["tag"])

        summary.extend([
            "",
            "<b>Выбран:</b>",
            html.escape(selected_name),
            f"{selected_row['delay']} ms · "
            f"{selected_row['speed']:.1f} Mbit/s",
        ])

    return "\n".join(summary)


def send_speedcache(chat_id, title="VPN Optimizer — speed-cache"):
    report = speedcache_report_html(title)

    if report is None:
        send_message(
            chat_id,
            "Ночного speed-cache пока нет."
        )
        return

    send_message(chat_id, report, parse_mode="HTML")


def notify_nightly_report():
    report = speedcache_report_html("VPN Optimizer — ночная оптимизация")

    if report is None:
        return notify_allowed_users(
            "VPN Optimizer: ночная оптимизация завершилась без speed-cache."
        )

    ok = True

    for chat_id in sorted(ALLOWED_USERS):
        for attempt in range(3):
            try:
                send_message(chat_id, report, parse_mode="HTML")
                break
            except Exception as error:
                if attempt < 2:
                    time.sleep(5)
                else:
                    ok = False
                    print(
                        f"NOTIFY ERROR ({type(error).__name__})",
                        flush=True,
                    )

    return ok


def notify_nightly_error(message):
    return notify_allowed_users(
        "VPN Optimizer: ночная оптимизация — ошибка\n" + message
    )


HELP_TEXT = f"""VPN-бот.

Домены:
Отправь домен или URL без команды — добавить его в VPN.
/list — показать домены, направляемые через VPN
/check example.com — проверить, есть ли домен в VPN-списке
/del example.com — удалить домен из VPN-списка

VPN Optimizer:
/speed — 3 загрузки до 5 МиБ через текущий сервер: результаты, медиана, delay и класс. Без смены сервера
/recheck — проверить скорость до 18 кандидатов с сохранёнными задержками и при необходимости переключиться. Во время теста VPN-маршрутизация клиентов временно отключена
/switch — перейти на лучшую альтернативу из сохранённой статистики без нового замера; HTTPS-проверка и возврат при неудаче
/speedcache — показать последнее сохранённое измерение
/exclude — исключить текущий сервер и выбрать новый
/excluded — показать исключённые серверы
/restore N — вернуть сервер №N из списка исключённых
/restoreall — вернуть все исключённые серверы

/help или /? — показать эту справку

Автоматическая работа VPN Optimizer:
Ночью около {CONFIG["NIGHT_HOUR"]}:00 ({CONFIG["NIGHT_TZ"]}) — обновление подписки, измерение задержек и скорости, выбор сервера.
Watchdog примерно раз в минуту проверяет Discord через VPN, раз в час измеряет скорость (5 МиБ). Два результата ниже 5 Мбит/с запускают поиск замены."""


def handle_speed_current(chat_id):
    send_message(chat_id, "Проверяю скорость текущего VPN Optimizer-сервера…")

    result = run_speed_current()
    description = describe_tag(result["tag"])
    delay_text = f"{result['delay']} ms" if result["delay"] > 0 else "неизвестен"

    warning = ""
    if result["speed"] < 5.0:
        warning = "\n\nМедианная скорость ниже порога предупреждения 5 Mbit/s."

    samples = " / ".join(f"{value:.1f}" for value in result["samples"])

    send_message(
        chat_id,
        "VPN Optimizer — текущая скорость\n\n"
        f"Сервер: {description}\n"
        f"Delay: {delay_text}\n"
        f"Замеры: {samples} Mbit/s\n"
        f"Медиана: {result['speed']:.1f} Mbit/s\n"
        f"Class: {result['class']}"
        f"{warning}"
    )


def handle_speed_recheck(chat_id):
    send_message(
        chat_id,
        "Запускаю recheck скорости. На время измерений VPN-маршрутизация "
        "для домашних клиентов будет временно отключена. Это может занять несколько минут."
    )

    return_code, output = run_speed_recheck()

    if return_code != 0:
        lines = [line for line in output.splitlines() if line.strip()]
        detail = "\n".join(lines[-5:]) if lines else "неизвестная ошибка"
        send_message(chat_id, "VPN Optimizer — recheck завершился с ошибкой:\n" + detail)
        return

    report = speedcache_report_html("VPN Optimizer — recheck скорости")

    if report is None:
        send_message(chat_id, "Recheck завершён, но speed-cache не найден.")
        return

    send_message(chat_id, report, parse_mode="HTML")


def handle_excluded(chat_id):
    entries = get_excluded_entries()

    if not entries:
        send_message(chat_id, "Список исключённых VPN Optimizer-серверов пуст.")
        return

    lines = ["Исключённые VPN Optimizer-серверы:", ""]

    for index, (tag, member) in enumerate(entries, start=1):
        lines.append(f"{index}. {format_member(member, tag)}")

    lines.append("")
    lines.append("Вернуть сервер: /restore N")
    lines.append("Вернуть все: /restoreall")

    send_message(chat_id, "\n".join(lines))


def handle_restore(chat_id, argument):
    try:
        index = int(argument)
    except Exception:
        raise ValueError("используй /restore N, где N — номер из /excluded")

    entries = get_excluded_entries()

    if not entries:
        send_message(chat_id, "Список исключённых VPN Optimizer-серверов пуст.")
        return

    if index < 1 or index > len(entries):
        raise ValueError(f"номер должен быть от 1 до {len(entries)}")

    tag, member = entries[index - 1]
    description = format_member(member, tag)

    subscription_action("restore", [tag])
    return_code, output = run_reselect()

    current = get_active_member()
    current_description = describe_tag(current)

    if return_code == 0:
        send_message(
            chat_id,
            f"Возвращён в подписку:\n{description}\n\n"
            f"Текущий сервер:\n{current_description}"
        )
    else:
        detail = output.splitlines()[-1] if output else "неизвестная ошибка"
        send_message(
            chat_id,
            f"Возвращён в подписку:\n{description}\n\n"
            f"Автовыбор завершился с ошибкой: {detail}"
        )


def handle_restore_all(chat_id):
    entries = get_excluded_entries()

    if not entries:
        send_message(chat_id, "Список исключённых VPN Optimizer-серверов уже пуст.")
        return

    tags = [tag for tag, _ in entries]
    count = len(tags)

    subscription_action("restore", tags)
    return_code, output = run_reselect()

    current = get_active_member()
    current_description = describe_tag(current)

    if return_code == 0:
        send_message(
            chat_id,
            f"Все исключённые VPN Optimizer-серверы возвращены: {count}.\n\n"
            f"Текущий сервер:\n{current_description}"
        )
    else:
        detail = output.splitlines()[-1] if output else "неизвестная ошибка"
        send_message(
            chat_id,
            f"Все исключённые VPN Optimizer-серверы возвращены: {count}.\n\n"
            f"Автовыбор завершился с ошибкой: {detail}"
        )


def handle_exclude_current():
    before = get_subscription()
    current = get_active_member()

    if not current:
        raise RuntimeError("не удалось определить текущий VPN Optimizer-сервер")

    old_description = describe_tag(current, before)

    subscription_action("exclude", [current])

    return_code, output = run_reselect()

    after = get_subscription()
    new_current = get_active_member()
    new_description = describe_tag(new_current, after)

    if return_code == 0:
        text = (
            "VPN Optimizer: сервер исключён вручную\n"
            f"Исключён: {old_description}\n"
            f"Выбран: {new_description}\n"
            "Quick/deep-check: OK"
        )
    else:
        detail = output.splitlines()[-1] if output else "неизвестная ошибка"

        text = (
            "VPN Optimizer: сервер исключён вручную, но автовыбор завершился с ошибкой\n"
            f"Исключён: {old_description}\n"
            f"Текущий: {new_description}\n"
            f"Ошибка: {detail}"
        )

    notify_allowed_users(text)


def notify_subscription_refresh():
    sub = get_subscription()

    label = (sub.get("label") or "VPN Optimizer").strip()
    members = len(sub.get("memberTags") or [])
    excluded = len(sub.get("excludedTags") or [])

    notify_allowed_users(
        f"{label}: подписка обновлена\n"
        f"Серверов: {members}\n"
        f"Исключено: {excluded}"
    )


def notify_server_switch(old_tag, new_tag, reason, delay="unknown", protocol=""):
    if not new_tag or old_tag == new_tag:
        return

    sub = get_subscription()

    old_description = describe_tag(old_tag, sub)
    new_description = describe_tag(new_tag, sub)

    reason_names = {
        "quick-check": "quick-check не пройден",
        "deep-check": "deep-check не пройден",
        "optimize": "суточная оптимизация",
        "refresh": "обновление подписки",
    }

    reason_text = reason_names.get(reason, reason or "переключение")

    delay_text = str(delay).strip()

    if delay_text and delay_text not in ("unknown", "999999"):
        delay_line = f"\nDelay нового сервера: {delay_text} ms"
    else:
        delay_line = ""

    notify_allowed_users(
        "VPN Optimizer: сервер изменён\n"
        f"Причина: {reason_text}\n"
        f"Было: {old_description}\n"
        f"Стало: {new_description}"
        f"{delay_line}\n"
        "Deep-check: OK"
    )


def notify_speed_low(tag, speed1, speed2, delay):
    try:
        description = describe_tag(tag)
    except Exception:
        description = tag

    delay_text = f"{delay} ms" if str(delay) not in ("", "0", "999999", "unknown") else "неизвестен"

    return notify_allowed_users(
        "VPN Optimizer — низкая скорость\n\n"
        f"Текущий сервер: {description}\n"
        f"Speed: {speed1} Mbit/s\n"
        f"Повтор через 60 сек: {speed2} Mbit/s\n"
        f"Delay: {delay_text}\n"
        "Порог: 5 Mbit/s\n\n"
        "Сервер остаётся выбранным. Автоматическое переключение не выполнялось."
    )


def notify_speed_restored(tag, speed, delay):
    try:
        description = describe_tag(tag)
    except Exception:
        description = tag

    delay_text = f"{delay} ms" if str(delay) not in ("", "0", "999999", "unknown") else "неизвестен"

    return notify_allowed_users(
        "VPN Optimizer — скорость восстановилась\n\n"
        f"Текущий сервер: {description}\n"
        f"Speed: {speed} Mbit/s\n"
        f"Delay: {delay_text}\n"
        "Состояние снова выше порога 5 Mbit/s."
    )


def process_message(message):
    sender_id = message.get("from", {}).get("id")
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    # Работаем только с разрешёнными пользователями
    # и только в личных чатах.
    if sender_id not in ALLOWED_USERS:
        return

    if chat.get("type") != "private":
        return

    text = message.get("text")

    if not text:
        send_message(chat_id, "Отправь домен, URL или команду текстом.")
        return

    text = text.strip()

    if text in ("/start", "/help", "/?"):
        send_message(chat_id, HELP_TEXT)
        return

    if text == "/speed":
        handle_speed_current(chat_id)
        return

    if text == "/recheck":
        handle_speed_recheck(chat_id)
        return

    if text == "/switch":
        handle_switch_cached(chat_id)
        return

    if text == "/speedcache":
        send_speedcache(chat_id)
        return

    if text == "/excluded":
        handle_excluded(chat_id)
        return

    if text == "/exclude":
        handle_exclude_current()
        return

    if text == "/restoreall":
        handle_restore_all(chat_id)
        return

    if text == "/restore":
        send_message(chat_id, "Используй /restore N, где N — номер из /excluded.")
        return

    if text.startswith("/restore "):
        handle_restore(chat_id, text[9:].strip())
        return

    if text == "/list":
        domains = get_domains()

        if not domains:
            send_message(chat_id, "VPN-список пуст.")
        else:
            send_message(
                chat_id,
                "Через VPN:\n\n" + "\n".join(domains)
            )

        return

    if text.startswith("/check "):
        domain = normalize_domain(text[7:])
        domains = get_domains()

        if domain in domains:
            send_message(chat_id, f"{domain} уже есть в VPN-списке.")
        else:
            send_message(chat_id, f"{domain} в VPN-списке нет.")

        return

    if text.startswith("/del "):
        domain = normalize_domain(text[5:])
        domains = get_domains()

        if domain not in domains:
            send_message(chat_id, f"{domain} в VPN-списке нет.")
            return

        before = list(domains)
        domains.remove(domain)
        set_domains(domains, expected=before)

        send_message(chat_id, f"Удалено: {domain}")
        return

    if text.startswith("/"):
        send_message(chat_id, "Неизвестная команда. Используй /help или /?.")
        return

    domain = normalize_domain(text)
    domains = get_domains()

    if domain in domains:
        send_message(chat_id, f"{domain} уже есть в VPN-списке.")
        return

    before = list(domains)
    domains.append(domain)
    set_domains(domains, expected=before)

    send_message(chat_id, f"Добавлено через VPN: {domain}")


def load_offset():
    try:
        return int(read_text(OFFSET_FILE))
    except Exception:
        return None


def save_offset(offset):
    temp = OFFSET_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        f.write(str(offset) + "\n")

    os.replace(temp, OFFSET_FILE)
    os.chmod(OFFSET_FILE, 0o600)


def discard_old_updates():
    updates = telegram(
        "getUpdates",
        {"timeout": "0"},
        timeout=15,
    )

    if updates:
        offset = max(x["update_id"] for x in updates) + 1
    else:
        offset = 0

    save_offset(offset)

    return offset


def main():
    offset = load_offset()

    if offset is None:
        offset = discard_old_updates()

    print("Telegram VPN bot started", flush=True)

    while True:
        try:
            updates = telegram(
                "getUpdates",
                {
                    "offset": str(offset),
                    "timeout": "50",
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=65,
            )

            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                message = update.get("message")

                if not message:
                    continue

                try:
                    process_message(message)

                except ValueError as error:
                    sender_id = message.get("from", {}).get("id")
                    chat_id = message.get("chat", {}).get("id")

                    if sender_id in ALLOWED_USERS:
                        send_message(
                            chat_id,
                            "Некорректные данные или несовместимый VPN rule-set. Проверь настройки и /help."
                        )

                except Exception as error:
                    sender_id = message.get("from", {}).get("id")
                    chat_id = message.get("chat", {}).get("id")

                    if sender_id in ALLOWED_USERS:
                        send_message(
                            chat_id,
                            f"Ошибка AWG/бота ({type(error).__name__}). Проверь локальные API и настройки."
                        )

        except Exception as error:
            print(f"ERROR ({type(error).__name__})", flush=True)
            time.sleep(5)


def cli_main(argv):
    if len(argv) >= 2 and argv[1] == "--notify-nightly":
        sys.exit(0 if notify_nightly_report() else 1)

    if len(argv) >= 3 and argv[1] == "--notify-nightly-error":
        sys.exit(0 if notify_nightly_error(" ".join(argv[2:])) else 1)

    if len(argv) >= 2 and argv[1] == "--notify-refresh":
        notify_subscription_refresh()
        return

    if len(argv) >= 6 and argv[1] == "--notify-speed-low":
        ok = notify_speed_low(argv[2], argv[3], argv[4], argv[5])
        sys.exit(0 if ok else 1)

    if len(argv) >= 5 and argv[1] == "--notify-speed-restored":
        ok = notify_speed_restored(argv[2], argv[3], argv[4])
        sys.exit(0 if ok else 1)

    if len(argv) >= 5 and argv[1] == "--notify-switch":
        old_tag = argv[2]
        new_tag = argv[3]
        reason = argv[4]
        delay = argv[5] if len(argv) >= 6 else "unknown"
        protocol = argv[6] if len(argv) >= 7 else ""

        notify_server_switch(
            old_tag,
            new_tag,
            reason,
            delay,
            protocol,
        )
        return

    main()


if __name__ == "__main__":
    cli_main(sys.argv)
