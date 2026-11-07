#!/opt/bin/python3
"""Read-only checks for this installation layout; no switching or refresh."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/vpn-optimizer"))
from common import CONFIG_FILE, load_config, valid_tag, validate_ruleset


def get_json(opener, url):
    with opener.open(url, timeout=10) as response:
        return json.load(response)


def api_data(opener, url):
    result = get_json(opener, url)
    if not isinstance(result, dict) or result.get("success") is not True:
        raise ValueError("API returned an unsuccessful response")
    return result.get("data")


def find_subscriptions(value, sub_id):
    if isinstance(value, dict):
        if value.get("id") == sub_id:
            yield value
        for child in value.values():
            yield from find_subscriptions(child, sub_id)
    elif isinstance(value, list):
        for child in value:
            yield from find_subscriptions(child, sub_id)


def check_selector(selector, subscriptions, sub_id):
    matches = list(find_subscriptions(subscriptions, sub_id))
    if len(matches) != 1 or not isinstance(selector, dict):
        raise ValueError("Subscription or selector is missing/ambiguous")
    members = selector.get("all")
    tags = matches[0].get("memberTags")
    if (not isinstance(members, list) or not members or not isinstance(tags, list)
            or any(not valid_tag(tag) for tag in members + tags)
            or selector.get("now") not in members or not set(members).issubset(tags)):
        raise ValueError("Unsupported selector/memberTags format or subscription mismatch")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=CONFIG_FILE)
    args = parser.parse_args(argv)
    errors = 0

    def check(label, action):
        nonlocal errors
        try:
            action()
        except Exception as error:
            # External exceptions can include URLs, IDs or entire API responses.
            print(f"ERROR: {label} ({type(error).__name__}); проверь соответствующий пункт INSTALL_RU.md")
            errors += 1
        else:
            print("OK: " + label)

    try:
        config = load_config(args.config)
    except ValueError as error:
        print("ERROR: " + str(error))
        return 1
    print("OK: конфигурация, владелец и права; значения скрыты")

    def components():
        if os.geteuid() != 0 or not Path("/opt/bin/python3").is_file():
            raise ValueError("Run with Entware Python as root")
        for directory in ("/opt/bin", "/opt/etc/init.d", "/opt/lib", "/opt/var/run", "/opt/var/log", "/opt/var/lib"):
            info = Path(directory).stat()
            if not Path(directory).is_dir() or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Missing or writable system directory")
        for command in ("curl", "awk", "sed", "grep", "sort", "tr", "cut", "head", "wc", "date",
                        "mkdir", "rmdir", "mktemp", "touch", "cat", "mv", "rm", "sleep", "nohup", "pidof"):
            if not shutil.which(command):
                raise ValueError("Missing command: " + command)
        if not shutil.which("sing-box") and subprocess.run(
                ["pidof", "sing-box"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise ValueError("sing-box is neither in PATH nor running")
    check("Entware, Python, sing-box, системные утилиты и каталоги", components)

    def clock():
        env = os.environ.copy()
        if config["NIGHT_TZ"] != "LOCAL":
            env["TZ"] = config["NIGHT_TZ"]
        result = subprocess.run(["date", "+%Y-%m-%d %H %z"], env=env, check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, text=True)
        offset = result.stdout.strip().split()[-1]
        zone = config["NIGHT_TZ"]
        expected = {"MSK-3": "+0300", "UTC0": "+0000"}.get(zone)
        if zone.startswith(("UTC+", "UTC-")):
            hour, _, minute = zone[4:].partition(":")
            expected = ("-" if zone[3] == "+" else "+") + hour.zfill(2) + (minute or "00")
            if int(hour) == 0 and int(minute or 0) == 0:
                expected = "+0000"
        if not re.fullmatch(r"[+-][0-9]{4}", offset) or (expected is not None and offset != expected):
            raise ValueError("System date does not support this timezone")
    check("команда date с выбранным часовым поясом", clock)

    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    awgm = config["AWGM_URL"]

    def selector():
        data = api_data(direct, awgm + "/api/singbox/subscriptions")
        url = config["CLASH_URL"] + "/proxies/" + urllib.parse.quote(config["SELECTOR"], safe="")
        with direct.open(url, timeout=10) as response:
            raw = response.read()
        selected = json.loads(raw)
        check_selector(selected, data, config["SUB_ID"])
        # Verify the actual shell parser, without changing the response format.
        parsed = subprocess.run(["/bin/sh", "-c", r'''sed -n 's/.*"all":[[:space:]]*\[\([^]]*\)\].*/\1/p' | tr ',' '\n' | sed 's/[[:space:]"]//g' '''],
                                input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if parsed.returncode or parsed.stdout.decode().split() != selected["all"]:
            raise ValueError("Clash response is incompatible with the shell parser")
        settings = api_data(direct, awgm + "/api/singbox/router/settings")
        if (not isinstance(settings, dict) or type(settings.get("enabled")) is not bool
                or (settings["enabled"] and not isinstance(settings.get("routingMode"), str))):
            raise ValueError("Unsupported routing settings response")
    check("AWG Manager, Clash, подписка и selector", selector)
    check("выделенный inline rule-set только с domain_suffix", lambda: validate_ruleset(
        api_data(direct, awgm + "/api/singbox/router/rulesets/list"), config["RULESET_TAG"]))

    def proxy():
        # Same curl/proxy/HTTPS path as the watchdog, without switching servers.
        result = subprocess.run(["curl", "-x", config["PROXY_URL"], "--connect-timeout", "5",
                                 "--max-time", "10", "-fsS", "https://discord.com/api/v10/gateway"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if result.returncode or b"gateway.discord.gg" not in result.stdout:
            raise ValueError("Proxy HTTPS check failed")
    check("HTTPS через локальный прокси (короткий запрос Discord)", proxy)

    cron = Path("/opt/var/spool/cron/crontabs/root")
    lines = cron.read_text().splitlines() if cron.is_file() else []
    jobs = [line for line in lines if line.strip() and not line.lstrip().startswith("#")
            and "/opt/bin/vpn-optimize.sh" in line]
    if len(jobs) > 1:
        print("ERROR: несколько заданий optimizer; удали дубли вручную")
        errors += 1
    elif jobs:
        print("OK: найдено одно задание optimizer; crontab не изменён")
    else:
        print("WARNING: задание optimizer ещё не добавлено; crontab не изменён")
    if not shutil.which("pidof") or not any(Path(p).is_file() for p in ("/opt/sbin/crond", "/opt/sbin/cron")):
        print("ERROR: cron отсутствует или недоступна проверка его процесса")
        errors += 1
    elif all(subprocess.run(["pidof", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode for name in ("crond", "cron")):
        print("WARNING: cron/crond не запущен; настрой штатный cron по инструкции")
    else:
        print("OK: процесс cron/crond запущен; его crontab-каталог нужно проверить вручную")
    print("WARNING: перед копированием проверь конфликты существующих файлов и сделай резервную копию")
    print("WARNING: совместимость маршрутизатора и восстановление маршрутизации требуют ручной проверки")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"ERROR: preflight could not finish ({type(error).__name__}); details omitted")
        sys.exit(1)
