"""Small shared checks for the shell scripts, Telegram bot and preflight."""
import os
import re
import stat
import sys
from urllib.parse import urlsplit

CONFIG_FILE = "/opt/etc/vpn-optimizer.conf"
REQUIRED = ("SUB_ID", "SELECTOR", "RULESET_TAG", "AWGM_URL", "CLASH_URL",
            "PROXY_URL", "TG_BOT_TOKEN", "TG_ALLOWED_IDS")
SHELL_KEYS = ("SUB_ID", "SELECTOR", "AWGM_URL", "CLASH_URL", "PROXY_URL",
              "NIGHT_HOUR", "NIGHT_TZ")


def valid_tag(value):
    # Tags are embedded in shell-parsed tables, URL components and JSON.
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", value) is not None


def load_config(path=CONFIG_FILE):
    """Read data only, and never include a value in an error message."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise ValueError("Config must be a regular file owned by root")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("Config must have chmod 600")
            entries = {}
            for number, raw in enumerate(source, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if (not separator or key not in REQUIRED + ("NIGHT_HOUR", "NIGHT_TZ")
                        or key in entries or not value or value != value.strip()
                        or value.startswith("REPLACE_")):
                    raise ValueError(f"Invalid config entry at line {number}")
                entries[key] = value
    except (OSError, UnicodeError):
        raise ValueError("Cannot safely read config (file, owner, permissions or encoding)") from None
    for key in REQUIRED:
        if key not in entries:
            raise ValueError(f"Missing config key: {key}")
    for key in ("SUB_ID", "SELECTOR", "RULESET_TAG"):
        if not valid_tag(entries[key]):
            raise ValueError(f"Unsupported format: {key} (letters, digits, underscore, hyphen)")
    for key in ("AWGM_URL", "CLASH_URL", "PROXY_URL"):
        try:
            value = entries[key]
            url = urlsplit(value)
            if (url.scheme != "http" or not url.hostname or url.username is not None
                    or url.password is not None or url.path not in ("", "/")
                    or url.query or url.fragment or not url.port
                    or not re.fullmatch(r"http://[A-Za-z0-9.\[\]:-]+/?", value)):
                raise ValueError()
        except ValueError:
            raise ValueError(f"Invalid {key}: use http://host:port without credentials or path") from None
        entries[key] = value.rstrip("/")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", entries["TG_BOT_TOKEN"]):
        raise ValueError("Invalid TG_BOT_TOKEN format")
    if not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", entries["TG_ALLOWED_IDS"]):
        raise ValueError("Invalid TG_ALLOWED_IDS format")
    legacy = "NIGHT_HOUR" not in entries and "NIGHT_TZ" not in entries
    entries.setdefault("NIGHT_HOUR", "03")
    entries.setdefault("NIGHT_TZ", "MSK-3" if legacy else "LOCAL")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3])", entries["NIGHT_HOUR"]):
        raise ValueError("NIGHT_HOUR must be 00..23")
    # Fixed POSIX offsets only. The sign is opposite to the usual UTC notation.
    if not re.fullmatch(r"LOCAL|MSK-3|UTC0|UTC[+-](?:[0-9]|1[0-4])(?::[0-5][0-9])?", entries["NIGHT_TZ"]):
        raise ValueError("NIGHT_TZ: use LOCAL, MSK-3, UTC0 or a fixed POSIX offset such as UTC-3")
    return entries


def validate_ruleset(rulesets, tag):
    """Only an exclusively owned inline domain_suffix rule-set is writable."""
    error = "Dedicated rule-set must contain only tag, type=inline and domain_suffix rules"
    if not isinstance(rulesets, list) or any(not isinstance(item, dict) for item in rulesets):
        raise ValueError("Unsupported rule-set list response")
    matches = [item for item in rulesets if item.get("tag") == tag]
    if len(matches) != 1:
        raise ValueError("Dedicated rule-set is missing or its tag is duplicated")
    item = matches[0]
    if not {"tag", "type", "rules"}.issubset(item) or item["type"] != "inline" or not isinstance(item["rules"], list):
        raise ValueError(error)
    domains = []
    for rule in item["rules"]:
        if not isinstance(rule, dict) or set(rule) != {"domain_suffix"} or not isinstance(rule["domain_suffix"], list):
            raise ValueError(error)
        for domain in rule["domain_suffix"]:
            if not isinstance(domain, str) or not re.fullmatch(
                    r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", domain):
                raise ValueError("Unsupported domain_suffix value in dedicated rule-set")
            domains.append(domain.lower())
    return sorted(set(domains))


if __name__ == "__main__":
    try:
        config = load_config(sys.argv[2] if len(sys.argv) == 3 else CONFIG_FILE)
        if len(sys.argv) >= 2 and sys.argv[1] == "--shell":
            for key in SHELL_KEYS:
                print(key + "=" + config[key])
        else:
            print("OK: config is valid; values not displayed")
    except ValueError as error:
        print("VPN Optimizer: " + str(error), file=sys.stderr)
        sys.exit(1)
