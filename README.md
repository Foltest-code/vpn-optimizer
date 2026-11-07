# VPN Optimizer

**English** · [Русский](#русский)

VPN Optimizer monitors and selects VPN servers on a router with **Entware, AWG Manager and sing-box**. It combines HTTPS connectivity checks with actual data transfers and download-speed measurements: a server with low latency may still be too slow or unable to carry normal traffic.

The project works with an **existing VPN subscription**, a configured local HTTP proxy and router routing rules. It **does not install or configure the VPN infrastructure**.

### Features

- Scheduled subscription refresh, server measurements and automatic selection.
- Watchdog checks of connectivity and throughput, with recovery attempts.
- Telegram notifications and commands for checking, switching and managing servers.
- Configurable overnight schedule; manual installation, updates and rollback.

### Before installing

Requires Entware under `/opt`, root access, Python 3, curl, cron, a functioning AWG Manager/sing-box setup and the expected local APIs. Compatibility with arbitrary router models and component versions is **not guaranteed**.

**Important:** During overnight measurements and `/recheck`, VPN routing for client devices is temporarily disabled. Connections may be interrupted, and traffic may use the normal direct route until routing is restored. If the management API fails, manual recovery may be needed.

**v2.5.1** has been exercised on a real router: cron-triggered optimization, server selection, TPROXY restoration and Telegram report delivery were verified. Automated tests have additional limitations; see `TEST_RESULTS.txt`.

**Documentation:** [Overview](README_EN.md) · [Installation / upgrade / rollback](INSTALL_EN.md) · [AI-assisted setup](AI_INSTALL_PROMPT_EN.md) · [Security notes (RU)](SECURITY_AUDIT_RU.md) · [Latest release](../../releases/latest)

### Русский

**VPN Optimizer** выбирает и контролирует VPN-серверы на маршрутизаторе с **Entware, AWG Manager и sing-box**. Программа проверяет не только задержку и HTTPS-доступность, но и реальную передачу данных и скорость: сервер с небольшим ping может плохо передавать пользовательский трафик.

Используются **уже работающая VPN-подписка**, локальный HTTP-прокси и настроенная маршрутизация. **Сам VPN и правила маршрутизации программа не устанавливает.**

**Возможности:** ночная оптимизация и выбор сервера, watchdog с проверками доступности и скорости, автоматические попытки восстановления, Telegram-бот, настройка ночного времени, ручное обновление и откат.

**Требования:** Entware в `/opt`, права root, Python 3, curl, cron, работающие AWG Manager и sing-box, необходимые локальные API. Совместимость со всеми моделями роутеров и версиями компонентов **не гарантируется**.

**Важно:** во время ночных измерений и команды `/recheck` VPN-маршрутизация клиентских устройств временно отключается. Возможны обрыв соединений и выход клиентского трафика напрямую до восстановления маршрутизации. При сбое API может потребоваться ручное вмешательство.

**v2.5.1** проверена на реальном роутере: подтверждены запуск через cron, выбор сервера, восстановление TPROXY и доставка отчёта Telegram. Ограничения автоматических тестов описаны в `TEST_RESULTS.txt`.

**Документация:** [Описание](README_RU.md) · [Установка / обновление / откат](INSTALL_RU.md) · [Установка с ИИ](AI_INSTALL_PROMPT_RU.md) · [Аудит безопасности](SECURITY_AUDIT_RU.md) · [Последний выпуск](../../releases/latest)

---

**License / Лицензия:** [MIT](LICENSE). The repository contains the license; the existing v2.5.1 download archive has not been repackaged. / Лицензия находится в репозитории; архив v2.5.1 не пересобирался.
