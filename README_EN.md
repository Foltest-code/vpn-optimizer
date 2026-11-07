# VPN Optimizer v2.5.1

Release v2.5.1. The version has been tested on a router running Entware, AWG Manager and sing-box: scheduled optimization, server selection, TPROXY restoration and Telegram report delivery were verified. Notification retries were tested separately with simulated failures. Static and simulated test results are described in `TEST_RESULTS.txt`. Compatibility with specific hardware and component versions must be checked independently.

VPN Optimizer helps select and monitor VPN servers on a compatible router running Entware, AWG Manager and sing-box. It uses an existing VPN subscription and a configured local HTTP proxy. It does not install the VPN infrastructure or set up the router's general routing rules.

A low response time does not guarantee good speed. Moreover, a short test request may succeed even when ordinary user traffic transfers poorly. That is why Optimizer combines HTTPS availability checks, actual data transfers and download speed tests.

## How a server is selected

During the overnight cycle, Optimizer refreshes the subscription, measures delays and tests candidates via the configured proxy. Initially it considers six low-latency servers, adding the current server if necessary. The tests include HTTPS, downloading 64 KiB and download speed using up to 5 MiB. Limits: 18 speed measurements and 30 candidate attempts.

Speed classes: GOOD — at least 20 Mbit/s; RESERVE — 8 to under 20 Mbit/s; SLOW — under 8 Mbit/s. The primary overnight selection is the lowest delay among GOOD servers; when no GOOD server exists, the highest measured speed wins. The final validation and fallback logic remain as in v2.5. Trojan servers are excluded from automatic selection.

During the overnight measurement and `/recheck`, client VPN routing is temporarily disabled through AWG Manager. Connectivity may be interrupted and clients may temporarily use the normal direct route. The previous mode is restored after the test. If the API is unavailable, manual recovery may be necessary.

## Automatic operation

- Cron checks whether an overnight cycle is due every ten minutes. Optimization runs during the configured hour and does not repeat after a successful run for that day; failed attempts may be retried.
- The new configuration template sets `NIGHT_HOUR=03` and `NIGHT_TZ=LOCAL`. If both are absent, the legacy schedule of 03:00 Moscow time applies.
- Watchdog makes a short Discord HTTPS check and then waits 60 seconds. Checks are approximately once a minute, not tied to exact clock minutes.
- About once an hour, a 5 MiB speed test is run for the current server. Two results below 5 Mbit/s separated by 60 seconds trigger a replacement search with fresh measurements of candidates.
- Emergency switches retain quick/deep checks. The periodic 64 KiB test is not performed; quick/deep checks remain part of emergency switching.

## Telegram

The bot accepts commands only from users listed in its configuration and only in private chats.

| Command | Action |
|---|---|
| `/help` | Help and the configured overnight schedule |
| `/speed` | Three measurements of up to 5 MiB on the current server; median and speed class; no switch |
| `/speedcache` | Saved results of the previous measurements |
| `/switch` | Choose an alternative from recent speed-cache, run an HTTPS check and attempt rollback on failure; no new speed measurement |
| `/recheck` | Measure candidates from the existing delay-cache; temporarily disable client VPN routing |
| `/exclude`, `/excluded` | Exclude the current server from the subscription; list exclusions |
| `/restore N`, `/restoreall` | Restore excluded servers and invoke the existing automatic selection |
| `/list`, `/check example.org`, `/del example.org` | View, check and remove domains in the dedicated rule-set |
| Domain or URL without a command | Add a domain to the dedicated rule-set |

The domain rule-set must be reserved exclusively for Optimizer. Additional metadata on the rule-set object is allowed; unknown fields inside rules and unrelated rules block updates. Optimizer does not create this rule-set or automatically connect it to the router's general routing rules.

## Requirements and limitations

Entware at `/opt`, Linux `/proc`, root access, Python 3 with built-in HTTPS modules, curl, BusyBox-compatible shell utilities, cron and working AWG Manager/Clash APIs are required. APIs are expected to be local HTTP endpoints with no additional authentication; the proxy is HTTP. Tests use Discord and Cloudflare. Telegram also communicates through the proxy.

Supported ID/tag characters are Latin letters, digits, `_` and `-`, with no leading `-`. Arbitrary labels containing spaces and complex selector-list formatting are unsupported. Preflight validates the current API response; subsequent provider/API formatting changes may still affect the original algorithm.

This release does not establish a universal list of supported models, minimum RAM or compatibility with arbitrary AWG Manager/sing-box versions. VPN itself must work first. See the [security audit](SECURITY_AUDIT_RU.md) for limitations relating to power loss, process signals, editing rule-sets concurrently and manually changing system time.

Check prerequisites and environment compatibility using [INSTALL_EN.md](INSTALL_EN.md) before installation.

## Installation and updates

See [INSTALL_EN.md](INSTALL_EN.md) for preparation, initial installation, verification, configuration changes, manual updates, rollback and removal. For step-by-step AI-assisted setup from scratch or on an already configured VPN, see [AI_INSTALL_PROMPT_EN.md](AI_INSTALL_PROMPT_EN.md).

The live configuration is `/opt/etc/vpn-optimizer.conf`, owned by `root:root` with permissions `0600`. Persistent data is stored at `/opt/var/lib/vpn-optimizer/`. Updates preserve the configuration and speed-cache. There is no installer, automatic migrator or update manager. The documented upgrade procedure starts with this release; older historical installations are not migrated automatically.

## Development tests

On Linux with Python 3 and BusyBox:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests create temporary copies, mock curl and system time as needed and do not connect to the router. Simulated root ownership in temporary test files permits non-root execution; enforcement of real root ownership is tested separately with mocked metadata. See `TEST_RESULTS.txt` for the results and test-environment limitations.
