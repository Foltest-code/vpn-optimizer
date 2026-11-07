# Installing and Updating VPN Optimizer v2.5.1

**Release guide for v2.5.1.** The version has been tested on a router running Entware, AWG Manager and sing-box: scheduled optimization, server selection, TPROXY restoration and Telegram report delivery were verified. Compatibility with specific models and configurations is not guaranteed. Verify the environment and working VPN first, and follow Part A if starting from scratch.

## 1. What is VPN Optimizer, and why use it?

### Why run a VPN on a router?

Usually a VPN is installed on a phone or computer. But a household also has TVs, streaming boxes, tablets and other devices where a separate VPN app may be inconvenient or impossible.

With VPN configured on a router, the router sends selected connections through VPN and the rest directly. You do not need to configure every device separately.

### The main idea behind VPN Optimizer

You want VPN to just work, without constantly searching for a suitable server or switching manually when websites stop opening.

In practice, a VPN server may answer a short test and appear healthy even when sites and apps cannot use it. A server can also be reachable but transfer data far too slowly.

**VPN Optimizer checks actual data transfer, not just response time.** At your chosen time it tests and compares servers and selects a suitable one. During the day it monitors the current connection and tries to find a replacement when necessary. You can view results or start checks from Telegram.

### What do AWG Manager and sing-box do?

These components provide the router's VPN connection and traffic routing. AWG Manager already updates subscriptions and checks server availability, but a short response-time test cannot always show how well normal data traffic works.

**Optimizer complements these tools; it does not replace them.** First configure a working VPN, then install Optimizer for automated quality monitoring.

### Installing with an AI assistant

You can work through the setup with ChatGPT or another AI assistant. Open [AI_INSTALL_PROMPT_EN.md](AI_INSTALL_PROMPT_EN.md), paste its instructions into a new chat and attach this guide and `README_EN.md`. The prompt covers both setting up everything from scratch and adding Optimizer to an existing VPN. **Never upload your live configuration, Telegram token or secret subscription URL to the chat.**

## Part A. Preparing the router

> If Entware, AWG Manager, sing-box and VPN already work, do not reinstall them. Check the relevant requirements and prepare a dedicated rule-set.
>
> The steps below cover setup from scratch. Entware packages depend on CPU architecture, and UI menu names depend on software versions. Check the linked documentation before making system changes.

### 2. Router, storage, Entware and SSH

**Required:** hardware and firmware compatible with AWG Manager; Entware at `/opt`; root SSH access; Python 3; standard utilities; sufficient storage. AWG Manager's documentation requires Entware on external USB storage for this sing-box setup. **Recommended:** an ext4 drive and a router configuration backup.

1. **Check compatibility.** Identify the model, hardware revision, firmware version and processor architecture. Compare them with the [AWG Manager installation requirements](https://hoaxisr.github.io/awg-manager-docs/install/). Do not choose an Entware installer simply because another router model has a similar name. Universal hardware compatibility and minimum RAM for Optimizer have not been verified.
2. **Prepare storage.** Connect a USB drive with adequate free space. ext4 is recommended for this setup. **Formatting erases all data**: back up anything important first. Confirm that the web interface detects the drive.
3. **Enable firmware components.** In system management, locate the component selection screen. AWG Manager's instructions mention OPKG, WireGuard, Netfilter for TProxy and filesystem support for ext4. The actual labels and available components depend on firmware. Apply the changes, then confirm that the web interface is available again.
4. **Install Entware.** On supported systems, `opkg disk` in the web command-line interface (WebCLI) selects the drive, after which you enter the installer URL **for your CPU architecture**. Follow the exact URL and syntax in the [official installation guide](https://hoaxisr.github.io/awg-manager-docs/install/). Confirm successful completion in the system log.
5. **Connect over SSH.** Find the actual SSH port in the settings or logs. On **your computer**, run:

```sh
ssh -p SSH_PORT root@ROUTER_ADDRESS
```

Set your own root password (`passwd`, if supported by your Entware build). Do not expose SSH to the internet.

**In the router's SSH terminal**, check:

```sh
id -u
ls -ld /opt /opt/bin /opt/etc/init.d /opt/var
/opt/bin/opkg --version
/opt/bin/opkg print-architecture
date
```

Expect UID `0`, existing `/opt` directories, package manager information and a correct date and time. Fix Entware or SSH if any check fails.

6. **Install dependencies.** With the Entware package manager, install only missing Python 3, curl, HTTPS certificate packages and `coreutils-nohup` (needed by the watchdog service). Install an Entware cron package for overnight scheduling; for Vixie cron use `opkg install cron` if missing. Then run **in router SSH**:

```sh
/opt/bin/python3 -c 'import ssl, urllib.request, json, os; print("Python HTTPS modules: OK")'
curl --version
command -v awk sed grep sort tr cut head wc date mktemp tar cp chmod chown ps
mkdir -p /opt/lib /opt/var/run /opt/var/log /opt/var/lib
ls -ld /opt/bin /opt/etc/init.d /opt/lib /opt/var/run /opt/var/log /opt/var/lib
```

Expect `Python HTTPS modules: OK`, HTTPS support in curl and all required utilities. `mkdir -p` only creates missing standard directories; it does not replace existing ones. If a directory is not owned by root or can be written by untrusted users, investigate before changing permissions. Install missing packages and repeat the checks. Correct system time is needed for certificates and the overnight schedule.

### Verified hardware and software configuration

The **Hopper 4G+ (NC-2312, EA)** platform was used during development; the hardware model remained unchanged. The following is a **software-version snapshot confirmed on November 1, 2026**, while testing an earlier working Optimizer version. Components may be upgraded independently, so these exact versions must not be assumed to have remained unchanged at the v2.5.1 release date.

| Component | Recorded value |
|---|---|
| Router | Hopper 4G+ (NC-2312, EA) |
| Processor and architecture | MT7981, aarch64 |
| RAM | 486 MB |
| Router firmware | 5.01.C.7.0-4 |
| Linux kernel | 4.9-ndm-5 |
| AWG Manager | 2.17.3 |
| sing-box | 1.14.0-beta.17-awgm.13 |
| Python | 3.13.9 |
| BusyBox (Entware) | 1.37.0-6 |
| curl | 8.15.0 |
| Traffic routing mode | TPROXY |

**v2.5.1 validation:** scheduling through cron, speed measurements, server selection, TPROXY restoration, and Telegram report delivery were separately verified on a real router. This table documents an earlier observed equipment configuration, not an exhaustive compatibility list or a guarantee for particular combinations of versions. Verify actual component versions and VPN behavior before installing.

### 3. AWG Manager, sing-box, subscription and routing

**Required:** AWG Manager, sing-box, a VPN subscription with a supported selector, local HTTP APIs and an HTTP proxy, plus working TProxy. **Recommended:** start with one domain and one client device instead of redirecting the entire network immediately. **Not needed:** a second independent sing-box instance if AWG Manager already controls one.

#### 3.1. Install AWG Manager

1. Follow the [official installation guide](https://hoaxisr.github.io/awg-manager-docs/install/) to add the appropriate repository for your architecture. Avoid installation scripts from unverified sources.
2. After adding the official repository, run **in router SSH**:

```sh
/opt/bin/opkg update
/opt/bin/opkg install awg-manager
```

3. The installer should report the local web interface URL. Open it **from a computer on the local network**. The port is configurable; do not assume another guide's port applies to your installation.
4. Enable web UI authentication if your version supports it. Never expose the interface to the internet. Confirm that the settings page opens.

#### 3.2. Install sing-box and add a subscription

1. In AWG Manager, open **Settings → Usage level** and choose a level that exposes sing-box sections (in the referenced documentation, **Advanced** or higher).
2. Install sing-box **through AWG Manager**. Check its version and status in the UI. Do not launch a second independent instance. [sing-box overview](https://hoaxisr.github.io/awg-manager-docs/guide/singbox/).
3. In AWG Manager, open **Sing-box subscriptions → URL subscription** (labels may vary by version). Enter your own secret URL **only in the AWG Manager interface**, assign a name and choose **manual selection (selector)**. Automatic `urltest` does not provide the selected-server control required by Optimizer.
4. Save the subscription, refresh its servers, verify the list is not empty, and manually choose a server. Verify the selected server is displayed. [Subscription guide](https://hoaxisr.github.io/awg-manager-docs/guide/singbox/subscriptions/).

Do not paste the subscription URL into an AI chat or a diagnostic report.

#### 3.3. Configure TProxy and verify real traffic

1. Under **Settings → Usage level**, select the level exposing Sing-box Router / TProxy (called **Advanced/Expert** depending on interface translation and version).
2. Open **Routing → Sing-box: TProxy**. Check that Netfilter and TPROXY are available. If components are missing, enable them through the firmware's supported configuration flow.
3. For the first test, select **one client device** via an access policy. Create a rule in simple mode: **What to route → test domain → Where → your VPN subscription**. Do not immediately reroute the entire local network.
4. Apply the configuration, enable the engine and check its status. A saved but unapplied TProxy draft does not affect traffic.
5. Open the test site and transfer some data on the client device. In AWG Manager connection monitoring, confirm VPN egress rather than `direct`. Check that an ordinary site outside your VPN rule also works. **A successful delay test alone is insufficient.**

Refer to the [official TProxy walkthrough](https://hoaxisr.github.io/awg-manager-docs/guide/singbox/router-quickstart/) and [Sing-box Router reference](https://hoaxisr.github.io/awg-manager-docs/guide/singbox/router/) for current screenshots and menu labels.

#### 3.4. Record the Optimizer settings

| Setting | Where to find it |
|---|---|
| `SUB_ID` | Subscription ID in AWG Manager, **not** its secret URL |
| `SELECTOR` | Selector tag associated with that subscription |
| `AWGM_URL` | Local AWG Manager HTTP API address |
| `CLASH_URL` | Local Clash (sing-box) HTTP API address |
| `PROXY_URL` | Local sing-box HTTP proxy used for transfer tests |

Check the values in your own UI and local configuration. Do not copy ports from another installation without verification. An **HTTP proxy** is required; SOCKS5 alone is insufficient. The preflight also checks this proxy, APIs and HTTPS. This release does not support separate API authentication, HTTPS APIs or passwords embedded in URLs. Do not expose any API or proxy to the internet.

IDs and tags may contain Latin letters, digits, `_` and `-`, but must not begin with `-`. Do not blindly rename tags used elsewhere. **Do not install Optimizer until real VPN traffic works.**

### 4. Dedicated domain rule-set

**Required:** a separate `inline` rule-set used only by Telegram commands and connected to the VPN route. Do not put unrelated rules in it. It is **optional** to populate many domains beforehand.

1. Open **Routing → Sing-box: TProxy** and switch to expert rule display.
2. Under **Rule-sets**, choose **+ Set**, type `Inline`, tag such as `vpn-optimizer-domains`.
3. Add only `domain_suffix` with the test domain `example.org`, or leave it empty if the interface allows that. Save.
4. Under **Routing rules**, use **+ Rule** → condition: this set → action: route via your VPN subscription. **Apply** the changes.
5. Verify the test domain route and record the tag as `RULESET_TAG`.

Allowed rule-set object **as returned by the API** (an example of structure, not an import command):

```json
{"tag":"vpn-optimizer-domains","type":"inline","rules":[{"domain_suffix":["example.org"]}]}
```

Do not use a shared set containing IP, port or other rules. If your API adds unknown fields, the preflight may reject the configuration; do not remove other people's rules to get past the check. Once installed, only the bot should edit this set; concurrent manual edits are unsupported.

### 5. Telegram bot and your user ID

Create a separate bot through the [official BotFather](https://t.me/BotFather) using `/newbot`, and keep the token locally. See [Telegram documentation](https://core.telegram.org/bots/features#botfather). Never send the token to others, paste it into a browser URL or put it on the command line.

You need a numeric Telegram User ID, not an `@username`. Before running Optimizer, you can obtain it using your bot's [getUpdates](https://core.telegram.org/bots/api#getupdates). Send the exact message `vpn-optimizer-setup` to your new bot in a private chat, then run the following **in router SSH**. It prompts for the token and local proxy without echoing the input, does not save them, and prints only the ID. Do not publish that ID either.

```sh
/opt/bin/python3 - <<'PY'
import getpass, json, urllib.request
try:
    token = getpass.getpass('New bot token: ')
    proxy = getpass.getpass('Local HTTP proxy (http://host:port): ')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    with opener.open('https://api.telegram.org/bot' + token + '/getUpdates', timeout=15) as response:
        data = json.load(response)
    ids = {item['message']['from']['id'] for item in data.get('result', [])
           if item.get('message', {}).get('text') == 'vpn-optimizer-setup'
           and item['message'].get('chat', {}).get('type') == 'private'}
    if len(ids) != 1:
        print('Expected exactly one sender. Check your new bot and message.')
    else:
        print('Your User ID for local configuration:', next(iter(ids)))
except Exception:
    print('Could not obtain ID. Check token, proxy, HTTPS, and other bot processes.')
PY
```

Do not use one bot token in multiple simultaneous instances. The bot uses long polling; no webhook or competing update consumer should be active. After starting Optimizer, send a *new* `/help` message; earlier messages may have been discarded.

## Part B. Installing Optimizer

### 6. Transfer the files

On **your computer**, once the approved release archive is available:

```sh
scp -P SSH_PORT vpn-optimizer-v2.5.1.tar.gz root@ROUTER_ADDRESS:/tmp/
```

In **router SSH**:

```sh
umask 077
export PATH=/opt/bin:/opt/sbin:/usr/sbin:/usr/bin:/sbin:/bin
cd /tmp
tar -xzf vpn-optimizer-v2.5.1.tar.gz
SOURCE=/tmp/vpn-optimizer-v2.5.1
ls "$SOURCE/bin" "$SOURCE/lib/vpn-optimizer" "$SOURCE/tools"
```

The archive is extracted separately from the running installation. Do not keep tokens or backups in `$SOURCE`. The release includes `SHA256SUMS` for checking the supplied files.

Before copying files, verify the internal manifest (every entry must report OK):

```sh
(cd "$SOURCE" && sha256sum -c SHA256SUMS)
```

Stop on any failure and obtain the package again. A separate `SHA256SUMS` is published alongside the archive: run `sha256sum -c SHA256SUMS` in the download directory before extraction (on macOS: `shasum -a 256 -c SHA256SUMS`). Checksums verify integrity; they do not by themselves authenticate the author.

### 7. Configuration

Create a configuration file **only if it does not exist**:

```sh
if [ -e /opt/etc/vpn-optimizer.conf ] || [ -L /opt/etc/vpn-optimizer.conf ]; then
    echo 'Configuration already exists; not overwritten'
else
    (umask 077; set -C; cat "$SOURCE/etc/vpn-optimizer.conf.example" > /opt/etc/vpn-optimizer.conf)
fi
```

Open `/opt/etc/vpn-optimizer.conf` in an editor, for example with `vi`. Fill each placeholder with your own values. If `vi` is unfamiliar: press `i` to type, `Esc` then `:wq` and Enter to save. Secrets belong only in this file, not in source scripts.

```sh
chown 0:0 /opt/etc/vpn-optimizer.conf
chmod 600 /opt/etc/vpn-optimizer.conf
/opt/bin/python3 "$SOURCE/lib/vpn-optimizer/common.py"
```

Expected output: `OK: config is valid; values not displayed`. The file must be a regular file, not a symlink. Use one `KEY=VALUE` per line, without quotes, commands or trailing comments. `TG_ALLOWED_IDS` is a comma-separated list of positive numeric IDs without spaces. Token syntax checks do not prove that the token is valid.

### 8. Overnight schedule

The new template uses `NIGHT_HOUR=03` and `NIGHT_TZ=LOCAL`. The hour must be exactly two digits from `00` to `23`.

| `NIGHT_TZ` | Meaning |
|---|---|
| `LOCAL` | Router system time |
| `MSK-3` | Moscow, fixed UTC+03:00 |
| `UTC0` | UTC |
| `UTC-3` | UTC+03:00 |
| `UTC+5:30` | UTC−05:30 |

For fixed POSIX time zones, the sign is reversed relative to familiar UTC offsets. Supported hour offsets are 0–14, optionally with minutes `:00`–`:59`. Regional names such as `Europe/Moscow` and complex daylight-saving transition rules are not supported. `LOCAL` follows the router's system settings.

With both new keys absent, the old 03:00 Moscow schedule is retained. With only one absent, its new default (`03` or `LOCAL`) applies. Empty or invalid values cause a configuration error.

Cron checks readiness at minutes 00, 10, 20, 30, 40 and 50 each hour. A successful run blocks another run on the same date. The existing `nightly-last-date` stores the date, completion time and time zone; after a `NIGHT_TZ` change, another successful cycle is also blocked for 24 hours. Thus the next cycle may be skipped after a zone change. Error retries are unchanged.

Limitations: changing the router system time zone while using `LOCAL`, manually jumping the clock or a legacy date-only marker cannot fully prevent another cycle. Temporarily remove the cron job when making such changes and restore it after the next night. Do not delete the success marker merely to force a new run. Manual `--force` intentionally bypasses the schedule.

### 9. Checks before installation

```sh
/opt/bin/python3 "$SOURCE/tools/preflight.py"
```

Preflight reads configuration and local APIs, makes one short Discord HTTPS request through the selected proxy, and checks cron and required components. It does **not** change servers, rule-sets, subscriptions or routing, or run a speed measurement.

Resolve `ERROR` before proceeding; read and evaluate each `WARNING`. A missing cron entry is normal before first installation. If the dedicated set fails validation, check section 4. Never "fix" a shared rule-set by deleting unrelated rules.

Make sure the intended locations `/opt/bin/vpn-*.sh`, `/opt/lib/vpn-optimizer`, `/opt/etc/telegram-vpn-bot`, `S99vpn-watchdog` and `S99telegram-vpn-bot` are not owned by another application. If there is a conflict, stop: a filename alone does not prove ownership. To upgrade an existing version, follow the update section.

### 10. Back up

Also run this before an update. Backup directories contain secrets; do not include them in public release archives. For updates, first perform the stop procedure in section 15 and note which services were running.

```sh
umask 077
mkdir -p /opt/var/backups
BACKUP_DIR=$(mktemp -d /opt/var/backups/vpn-optimizer.XXXXXX) || exit 1
: > "$BACKUP_DIR/files.list"
for ITEM in opt/bin/vpn-optimize.sh opt/bin/vpn-delay-scan.sh opt/bin/vpn-watchdog.sh opt/bin/vpn-watchdog-loop.sh opt/lib/vpn-optimizer opt/etc/telegram-vpn-bot opt/etc/init.d/S99vpn-watchdog opt/etc/init.d/S99telegram-vpn-bot opt/etc/vpn-optimizer.conf opt/var/lib/vpn-optimizer; do
    if [ -e "/$ITEM" ]; then printf '%s\n' "$ITEM" >> "$BACKUP_DIR/files.list"; fi
done
if [ -s "$BACKUP_DIR/files.list" ]; then
    (
        cd / || exit 1
        set --
        while IFS= read -r ITEM; do
            set -- "$@" "$ITEM"
        done < "$BACKUP_DIR/files.list"
        tar -czf "$BACKUP_DIR/files.tar.gz" "$@"
    ) || { echo 'Backup failed. Stop the installation.'; false; }
fi
if [ -f /opt/var/spool/cron/crontabs/root ]; then
    cp -p /opt/var/spool/cron/crontabs/root "$BACKUP_DIR/crontab.root"
fi
printf 'Backup directory: %s\n' "$BACKUP_DIR"
ls -l "$BACKUP_DIR"
```

If any command fails, do not copy new files. The backup passes filenames as arguments to remain compatible with BusyBox `tar`, which may not implement `-T`. Verify readability of the archive if created:

```sh
if [ -f "$BACKUP_DIR/files.tar.gz" ]; then tar -tzf "$BACKUP_DIR/files.tar.gz" >/dev/null; fi
```

Write down `$BACKUP_DIR`: shell variables are lost after reconnecting SSH. A separate copy of cron before its temporary removal during an upgrade is handled in section 15.

### 11. Copy and verify files

Only after checking conflicts and creating a backup:

```sh
mkdir -p /opt/lib/vpn-optimizer /opt/etc/telegram-vpn-bot /opt/var/lib/vpn-optimizer
chmod 700 /opt/lib/vpn-optimizer /opt/etc/telegram-vpn-bot /opt/var/lib/vpn-optimizer
cp "$SOURCE/lib/vpn-optimizer/common.py" /opt/lib/vpn-optimizer/common.py
cp "$SOURCE/bin/vpn-optimize.sh" "$SOURCE/bin/vpn-delay-scan.sh" "$SOURCE/bin/vpn-watchdog.sh" "$SOURCE/bin/vpn-watchdog-loop.sh" /opt/bin/
cp "$SOURCE/etc/telegram-vpn-bot/bot.py" /opt/etc/telegram-vpn-bot/bot.py
cp "$SOURCE/etc/init.d/S99vpn-watchdog" "$SOURCE/etc/init.d/S99telegram-vpn-bot" /opt/etc/init.d/
chmod 600 /opt/lib/vpn-optimizer/common.py
chmod 700 /opt/etc/telegram-vpn-bot/bot.py
chmod 755 /opt/bin/vpn-optimize.sh /opt/bin/vpn-delay-scan.sh /opt/bin/vpn-watchdog.sh /opt/bin/vpn-watchdog-loop.sh /opt/etc/init.d/S99vpn-watchdog /opt/etc/init.d/S99telegram-vpn-bot
```

The release does not copy a live configuration or persistent state over the installed versions.

```sh
SYNTAX_FAIL=0
for SCRIPT in /opt/bin/vpn-optimize.sh /opt/bin/vpn-delay-scan.sh /opt/bin/vpn-watchdog.sh /opt/bin/vpn-watchdog-loop.sh /opt/etc/init.d/S99vpn-watchdog /opt/etc/init.d/S99telegram-vpn-bot; do
    /bin/sh -n "$SCRIPT" || { echo "Shell syntax ERROR: $SCRIPT"; SYNTAX_FAIL=1; }
done
if [ "$SYNTAX_FAIL" -eq 0 ]; then
    /opt/bin/python3 -m py_compile /opt/etc/telegram-vpn-bot/bot.py /opt/lib/vpn-optimizer/common.py && \
    /opt/bin/python3 /opt/lib/vpn-optimizer/common.py
else
    echo 'ERROR: do not proceed until scripts are fixed.'
fi
```

Proceed only if every shell/Python syntax check and configuration validation succeeds.

### 12. Cron: add only your own job

`vpn-optimize.sh` explicitly exports an Entware-aware `PATH`: Vixie cron may use a restricted environment unlike an interactive SSH session, and `/bin/date` may not exist. If upgrading an earlier downloaded build without this line, update the script before relying on overnight execution.

Use the existing Entware cron service. Confirm it reads `/opt/var/spool/cron/crontabs`: inspect the installed service and the arguments of `cron` (Vixie) or `crond` (BusyBox). Merely seeing a `crond` process is not enough. If your cron uses another directory or per-user setup, the commands below do not apply.

Do not replace the general cron service. If it is stopped, start it using your package's supported mechanism after checking its configuration. Do not stop a shared cron service for Optimizer.

```sh
CRONFILE=/opt/var/spool/cron/crontabs/root
CRON_JOB='*/10 * * * * /opt/bin/vpn-optimize.sh # vpn-optimizer'
mkdir -p /opt/var/spool/cron/crontabs
touch "$CRONFILE"
chmod 600 "$CRONFILE"
if grep -Fq '/opt/bin/vpn-optimize.sh' "$CRONFILE"; then
    echo 'Optimizer entry already exists; check it; no duplicate added'
else
    printf '\n%s\n' "$CRON_JOB" >> "$CRONFILE"
fi
```

Check only the Optimizer entry; do not publish other users' jobs:

```sh
grep -F '/opt/bin/vpn-optimize.sh' "$CRONFILE"
```

With Entware Vixie cron (the `cron` package), run:

```sh
/opt/bin/crontab "$CRONFILE"
/opt/bin/crontab -l | grep -F '/opt/bin/vpn-optimize.sh'
```

Expect exactly one active `*/10 * * * *` entry. **For Vixie cron, register the modified file with** `/opt/bin/crontab "$CRONFILE"` and verify `/opt/bin/crontab -l`; editing its spool file directly does not guarantee that the daemon reloads it. For BusyBox `crond`, use its normal reload behavior. Verify actual execution after the next overnight window. Do **not** use `killall crond`.

### 13. Start and verify

```sh
/opt/etc/init.d/S99vpn-watchdog start
/opt/etc/init.d/S99telegram-vpn-bot start
/opt/etc/init.d/S99vpn-watchdog check
/opt/etc/init.d/S99telegram-vpn-bot check
```

Both `check` commands must report running services. Init scripts are in Entware's standard directory, but actual autostart depends on your router's Entware boot integration. Verify it after a planned reboot.

Send a *new* `/help` and then `/list` to the bot. Check the displayed schedule. `/speedcache` may have no results until the first overnight cycle. `/switch` needs a recent speed-cache; `/recheck` needs an existing delay-cache.

For an initial full cycle, either wait for the overnight run or consciously trigger it manually:

```sh
/opt/bin/vpn-optimize.sh --force
```

This command refreshes the subscription, switches servers, uses bandwidth and temporarily disables client VPN routing. Run it at a convenient time and let it finish. Do not close SSH during the check. Afterwards, verify `/speedcache`, actual client connectivity and the routing mode in AWG Manager. You may then check `/speed` and `/switch`.

Within the next hour, verify the scheduled speed measurement. After the next overnight window, check the report and confirm that successful runs are not duplicated. Do not simulate outages on an active network without a separate plan.

### 14. Change settings

First remove the Optimizer cron entry and stop its services as described in section 15. Back up, edit only `/opt/etc/vpn-optimizer.conf`, restore owner `root:root` and mode `0600`, run configuration validation and preflight. Then restart services and restore the cron entry as described in sections 12–13. The bot reads configuration at startup; editing the file alone does not reload it.

### 15. Update VPN Optimizer

These steps apply to releases starting from v2.5.1. The speed-cache format is preserved; add new configuration parameters manually if required by release notes. There is no automatic migrator.

First download and extract the new release separately (section 6), set `SOURCE` to its directory and read its upgrade notes. In router SSH:

1. Run `check` on both services and record which were running. Temporarily stop sending commands to the bot.
2. Back up the current crontab and remove only Optimizer's own marked entry:

```sh
umask 077
CRONFILE=/opt/var/spool/cron/crontabs/root
CRON_JOB='*/10 * * * * /opt/bin/vpn-optimize.sh # vpn-optimizer'
mkdir -p /opt/var/backups || exit 1
CRON_SAVED=$(mktemp /opt/var/backups/vpn-optimizer-cron.XXXXXX) || exit 1
cp -p "$CRONFILE" "$CRON_SAVED" || exit 1
CRON_TMP=$(mktemp /opt/var/spool/cron/crontabs/.vpn-optimizer.XXXXXX) || exit 1
awk -v job="$CRON_JOB" '$0 != job {print}' "$CRONFILE" > "$CRON_TMP" && chmod 600 "$CRON_TMP" && mv "$CRON_TMP" "$CRONFILE" || exit 1
```

Write down `$CRON_SAVED`; it contains private job data. If the entry was edited manually or other Optimizer schedules exist, remove only those entries by hand. Verify no active `/opt/bin/vpn-optimize.sh` job remains. Preserve unrelated jobs.

3. Stop the services:

```sh
/opt/etc/init.d/S99telegram-vpn-bot stop
/opt/etc/init.d/S99vpn-watchdog stop
ps | grep -E '[v]pn-(optimize|watchdog|delay-scan)|[t]elegram-vpn-bot'
ls -d /tmp/vpn-optimize.lock /tmp/vpn-watchdog.lock /tmp/vpn-delay-scan.lock 2>/dev/null
```

`stop` terminates the main process, but a child operation might still be running. Wait until checking processes and lock paths are gone. Do not remove locks or replace files while checks are still running. If a lock remains without a running process, inspect logs and AWG Manager state first; never start a parallel optimizer.

4. Run the backup procedure in section 10. Keep `$CRON_SAVED` inside `$BACKUP_DIR` as the before-update crontab:

```sh
cp -p "$CRON_SAVED" "$BACKUP_DIR/crontab.before-update"
```

5. Validate configuration and run the *new version's* preflight. Do not replace the user's configuration with the template. Add any required new parameters manually according to the release notes.
6. Copy files and run syntax checks from section 11. Do not delete persistent state directories or speed-cache contents.
7. Restore one cron job (section 12) and restart only the previously running services (section 13). Check Telegram, watchdog and retained speed-cache. Test main commands at a convenient time.

Repeating this procedure preserves configuration and does not duplicate cron jobs. Do not copy files until you know which application owns any existing paths.

### 16. Manual rollback

Remove the cron entry, stop the services and wait for active operations to finish (section 15). Supply the exact backup directory you recorded:

```sh
BACKUP_DIR=/opt/var/backups/YOUR_BACKUP_FOLDER
ls -l "$BACKUP_DIR"
tar -tzf "$BACKUP_DIR/files.tar.gz"
```

Confirm that this is your private backup for the correct installation, not a release archive. Restore the saved files:

```sh
tar -xzf "$BACKUP_DIR/files.tar.gz" -C / || exit 1
chown 0:0 /opt/etc/vpn-optimizer.conf
chmod 600 /opt/etc/vpn-optimizer.conf
```

The archive restores persistent state as it was at backup time. If the new release introduced files not present in the backup, remove **only** those specific files listed in that release's changes; never indiscriminately delete shared directories.

Do not blindly replace the entire current crontab with an old copy: unrelated jobs may have been added since then. Restore only Optimizer's prior entry if necessary. Restart only services that were previously running and repeat verification. If routing did not recover, first check and restore the intended mode in AWG Manager.

### 17. Uninstall

Back up first. Remove only the Optimizer cron entry and stop its services following section 15. Wait for operations to finish, and confirm that the following paths belong to this installation. Remove just the program files:

```sh
rm -f /opt/bin/vpn-optimize.sh /opt/bin/vpn-delay-scan.sh /opt/bin/vpn-watchdog.sh /opt/bin/vpn-watchdog-loop.sh
rm -f /opt/etc/init.d/S99vpn-watchdog /opt/etc/init.d/S99telegram-vpn-bot
rm -f /opt/etc/telegram-vpn-bot/bot.py /opt/lib/vpn-optimizer/common.py
```

The configuration, logs, bot update offset, backups and persistent state remain available for recovery. After verifying your backup, you may remove them separately:

```sh
rm -f /opt/etc/vpn-optimizer.conf
rm -rf /opt/var/lib/vpn-optimizer /opt/etc/telegram-vpn-bot /opt/lib/vpn-optimizer
rm -f /opt/var/run/vpn-delay.cache /opt/var/run/vpn-delay.last /opt/var/run/vpn-deep.last /opt/var/run/vpn-speed-monitor.last /opt/var/run/vpn-speed-monitor.state /opt/var/run/vpn-watchdog.bad /opt/var/run/vpn-watchdog-loop.pid /opt/var/run/telegram-vpn-bot.pid
rm -f /opt/var/log/vpn-watchdog.log /opt/var/log/vpn-watchdog-loop.log /opt/var/log/telegram-vpn-bot.log
```

AWG Manager, sing-box, the VPN subscription and the shared cron service remain installed. If no longer needed, disconnect and remove the dedicated rule-set manually in AWG Manager after checking other routing rules. Never delete unrelated rules or rule-sets.

### 18. Troubleshooting

| Symptom | What to check |
|---|---|
| Configuration error | root ownership, mode `0600`, regular file, no placeholders, valid formats |
| Preflight components | Section 2 commands, PATH, Python modules, running sing-box |
| Preflight API/selector | Your own API addresses/ports, API without extra authentication, subscription ID and selector match, valid tags |
| Preflight rule-set | Dedicated inline set with `tag/type/rules`, only `domain_suffix` within rules; extra API metadata at the top level is allowed, duplicate tags are not |
| Preflight HTTPS | Selected server, HTTP proxy, DNS, clock and certificates; this error alone does not prove a fault in Optimizer |
| Bot does not respond | Token, positive numeric User ID, private chat, fresh message after startup, Telegram through proxy, no second update consumer |
| Overnight cycle does not run | Cron directory, one entry, hour/time zone, successful-run marker, active locks |
| `/switch` or `/recheck` unavailable | Fresh speed-cache or existing delay-cache respectively; an initial full cycle may still be needed |
| Routing not restored after a check | Check AWG Manager routing mode and current server; if the API cannot be reached, manual recovery may be required |

Local logs: `/opt/var/log/vpn-watchdog.log`, `/opt/var/log/vpn-watchdog-loop.log`, `/opt/var/log/telegram-vpn-bot.log`. Logs may contain server names and network information. Do not publish them unredacted or attach a live configuration or backup. Full error API bodies and Telegram IDs are no longer intentionally printed.
