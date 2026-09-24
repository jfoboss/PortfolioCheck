---
paths:
  - "build_mac.sh"
  - "bootstrap_mac.sh"
  - "setup_env.sh"
  - "make_ca_bundle.sh"
  - "make_icon.py"
  - "secrets_store.py"
  - "paths.py"
  - "build_windows.py"
  - "osutil.py"
  - ".github/workflows/*"
---
# Сборка, секреты, хранилище

- **PyInstaller** в `build_mac.sh`: новые модули — в `--hidden-import`, новые каталоги данных — в `--add-data`
  (сейчас `docs:docs` — нужен для кнопки «Скопировать промпт для ChatGPT» в собранном .app).
- **Порядок: сначала правки `Info.plist`, потом `codesign`** (ad-hoc). Подпись до правок делает её невалидной, и
  Keychain отвечает `SecAuthFailure` на каждое обращение.
- `set -e` в скриптах: `grep` без совпадений возвращает 1 и молча обрывает сборку — оборачивай `|| true`.
- **Секреты** — через `/usr/bin/security` (не `keyring`): в собранном .app `keyring` ненадёжен. Значения
  кэшируются на время сессии, чтобы не дёргать Keychain на каждый запрос. Запасной вариант — файл с правами 600.
- **Хранилище** (`paths.py`): локально или iCloud Drive. Доступ к iCloud может запретить TCC (PermissionError) —
  тогда работаем локально и показываем баннер с кнопкой в «Конфиденциальность и безопасность».
- Bundle id — из переменной `BUNDLE_ID` (по умолчанию нейтральный `io.github.portfoliocheck`).
- **Windows**: `build_windows.py` (PyInstaller `--onedir`, zip), секреты — Credential Manager через `keyring`
  (`secrets_store.IS_WIN`), папка — `%APPDATA%\PortfolioCheck`. Новый модуль добавляй **в обе** сборки —
  `tests/test_platform.py` это проверяет. Бандл сертификатов собирается без консольного `openssl`
  (`settings_dialog.server_chain`).
- **CI** (`.github/workflows/ci.yml`): тесты на трёх ОС, `check_private.sh`, сборка Windows и запуск `.exe --demo`.
