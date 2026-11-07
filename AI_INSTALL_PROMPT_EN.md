# Installing VPN Optimizer v2.5.1 with an AI assistant

This file contains a ready-to-use prompt for ChatGPT or another AI assistant. Copy the text from **START OF AI PROMPT** into a new chat and attach `INSTALL_EN.md`, `README_EN.md` and, if available, `vpn-optimizer.conf.example`. If starting from scratch, screenshots of the router web UI will also be useful. **Never attach your live configuration, tokens or subscription URLs containing keys.**

---

## START OF AI PROMPT

I want to install **VPN Optimizer v2.5.1** on my router. The VPN infrastructure may not yet be installed. I need **step-by-step help from the beginning through verification**, including installing and configuring AWG Manager and sing-box if they are missing.

I am attaching `INSTALL_EN.md` and `README_EN.md`. Use these as the primary documentation for this specific Optimizer release. If you cannot access the files, ask me to upload them before giving Optimizer installation commands. Do not invent file contents or program features.

### 1. How to work with me

- Guide me through **one small step at a time**. Do not send a large batch of commands at once.
- Explain each step in plain language: what it does, why it is needed, exactly where to do it (router web UI / AWG Manager / computer terminal / router SSH), and what result to expect.
- After meaningful steps, ask for the command output or a screenshot **with all secrets hidden**, examine it and only then continue.
- Do not assume that I know Linux or terminal commands. If needed, explain SSH access, editing files and saving them.
- Do not install extra packages 'just in case' or change working settings without a reason. If part of the infrastructure already works, verify and reuse it.
- Do not replace an actual installation walkthrough with instructions like 'configure AWG Manager'. If it is missing, guide me through installing it step by step.
- **Ask only one question at a time**, and only when the answer is needed for the next action. Do not request a large questionnaire of settings.
- If a command's output differs from expectations, stop and investigate. Do not continue blindly.

### 2. Determine the starting point first

Ask for the router model and hardware revision, firmware version, whether USB storage is attached and resources are sufficient, SSH availability, and whether Entware, AWG Manager, sing-box, a VPN subscription and VPN routing already work.

Choose one path:

**Scenario A: setup from scratch.** One or more essential components are absent. Start with the router and VPN infrastructure (section 3), then move to Optimizer (section 5).

**Scenario B: VPN already works.** Do not reinstall anything automatically. First check required components and real data transfer (section 4), then install Optimizer (section 5).

If device compatibility is unverified or a critical resource is missing, explain the limitation rather than trying random workarounds.

### 3. Complete setup from scratch

Proceed through these stages in order, verifying each result:

1. **Router and access.** Determine its exact model, firmware, support for required components, web UI access and SSH method. Ensure the user can regain management access after changes. The Optimizer documentation does not establish universal supported models or a minimum RAM requirement.
2. **Storage and Entware.** Check whether external storage is required in this environment, its compatibility, filesystem, free space and connection method. Follow the official instructions for the actual router model to install and start Entware. Confirm `/opt`, appropriately privileged SSH access and the package manager. Never suggest formatting a drive containing data without explicit warning and consent.
3. **AWG Manager.** Consult the official installation documentation for the detected version and environment. Guide installation, service startup, opening the **local** web interface and initial setup. Do not invent addresses, ports, menu names or commands. If the version is unknown, determine it first; use screenshots when helping with UI steps.
4. **sing-box.** Install and configure it in the way supported by this AWG Manager version. First check whether AWG Manager already installed and manages it; a second parallel instance is unnecessary. Confirm startup, the local Clash API and local HTTP proxy needed by Optimizer.
5. **VPN subscription.** Explain how to add the user's own subscription in AWG Manager, refresh the server list, select a server and test the connection. Never ask the user to share the complete subscription URL, keys, server configuration or exports containing secrets.
6. **Routing and TProxy.** Using current documentation for the installed version, help enable required components and create a policy routing selected sites/services through VPN while other traffic follows the user's preferences. Explain consequences before changing general routing and back up existing settings first. Do not invent interface names, networks, IP addresses or firewall rules.
7. **Verify working VPN traffic.** From a client device, confirm that the selected resource really uses VPN, ordinary internet still works and actual data transfers through VPN—not just a short latency test. Verify VPN and direct behavior according to the user's routing rules. Check the local HTTP proxy. If VPN is not working, do not proceed to Optimizer.
8. **Dedicated rule-set.** In AWG Manager, create a separate `inline` rule-set with its own tag containing only supported `domain_suffix` rules, connect it to the VPN route and verify a test domain. Do not reuse a shared rule-set containing IP, port or unrelated rules. Follow the exact structure and requirements in `INSTALL_EN.md`.
9. **Telegram.** Help create a separate bot with the official BotFather and obtain the permitted numeric User ID using the method in `INSTALL_EN.md`. The user must enter the token only on their own device. Verify that no other process is using the token via long polling or webhook.

After completing these stages, briefly summarize what is installed, what has been verified and what remains unverified. Only then move to section 5.

### 4. If VPN is already configured

Do not reinstall Entware, AWG Manager or sing-box. Check their versions, required components, API availability, HTTP proxy and real VPN traffic from a client device. Confirm the subscription matches the selector and the dedicated rule-set exists without unrelated rules. If something is missing, return only to the necessary stage of section 3.

### 5. Install VPN Optimizer

Use **Part B of `INSTALL_EN.md`**, one step at a time:

1. Transfer the release archive, extract it and check the file list.
2. Create the private configuration **from the template** and fill it on the router without sharing secrets in chat.
3. Set `NIGHT_HOUR` and `NIGHT_TZ`; explain router local time versus an explicit time zone.
4. Verify `root:root` ownership, `0600` permissions and configuration validity.
5. Run `preflight.py`; investigate every `ERROR` and meaningful `WARNING`.
6. Check for path and service conflicts and back up files that might be touched.
7. Copy program files and run syntax checks.
8. Add **exactly one** entry to the existing cron configuration without replacing unrelated jobs or stopping the shared cron service.
9. Start and verify watchdog and the Telegram bot.
10. Verify `/help`, `/list`, available results and schedule. Before a full cycle or server switch, warn of possible brief service interruptions and route changes.
11. Verify VPN works from a client after testing, then plan to check the overnight cycle and autostart after a reboot.

Do not claim the overnight cycle or autostart has been verified unless these events have actually been observed.

### 6. If the program is already installed and I want to update it

Do not reinstall VPN components. Follow **'Update VPN Optimizer'** in `INSTALL_EN.md`: stop only Optimizer's services and jobs, wait for operations to finish, back up, replace only program files, preserve the private configuration and persistent data, manually add new settings if necessary, restart and verify. A temporary VPN interruption is acceptable. Do not introduce automatic migration or complicated zero-downtime upgrades.

If the new release has problems, follow the documented **manual rollback**. Do not remove unrelated files or shared cron jobs.

### 7. Security and source verification

- Never ask me to paste passwords, private keys, the Telegram token, a full VPN subscription URL, the live configuration or its backup into chat.
- Before reviewing screenshots or logs, ask me to hide secrets and personal identifiers. Do not run commands that dump the entire configuration.
- Never expose SSH, AWG Manager UI, Clash API or the local proxy to the public internet to make installation easier.
- For AWG Manager, sing-box, Entware and router firmware, **verify commands and menu labels against current official documentation** appropriate to the detected version. If browsing is unavailable, say so and ask for the official documentation link or a screenshot. Do not present guesses as verified steps.
- Do not run or recommend third-party installation scripts blindly; explain the source and the effect first.
- Explain actions that change storage, firmware, routing, firewall, VPN services or delete data, and request explicit confirmation before executing them.
- Do not invent Optimizer v2.5.1 APIs, commands, configuration keys or features not documented in the attached files.
- Before a full test or server switch, warn that VPN and client connectivity can be temporarily interrupted or traffic may use the direct route.

### 8. Start of the conversation

**Begin with one question:** What is the router's exact model and firmware version? After my answer, assess the current VPN setup and choose Scenario A or B. Do not output the entire installation guide in your first reply.

## END OF AI PROMPT
