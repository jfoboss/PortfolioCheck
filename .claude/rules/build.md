---
paths:
  - "build_mac.sh"
  - "bootstrap_mac.sh"
  - "setup_env.sh"
  - "make_ca_bundle.sh"
  - "make_icon.py"
  - "secrets_store.py"
  - "paths.py"
  - "build.py"
  - "osutil.py"
  - ".github/workflows/*"
---
# Сборка, секреты, хранилище

- **PyInstaller** в `build_mac.sh`: новые модули — в `--hidden-import`, новые каталоги данных — в `--add-data`
  (сейчас `docs:docs` — нужен для кнопки «Скопировать промпт для ChatGPT» в собранном .app).
- **Порядок: сначала правки `Info.plist`, потом `codesign`** (ad-hoc). Подпись до правок делает её невалидной, и
  Keychain отвечает `SecAuthFailure` на каждое обращение.
- `set -e` в скриптах: `grep` без совпадений возвращает 1 и молча обрывает сборку — оборачивай `|| true`.
- **Секреты на macOS** — через `/usr/bin/security` (не `keyring`): в собранном .app `keyring` ненадёжен. Значения
  кэшируются на время сессии, чтобы не дёргать Keychain на каждый запрос. Запасной вариант — файл с правами 600.
- **Хранилище** (`paths.py`): локальная папка ОС, iCloud Drive (только macOS) или своя папка. Доступ к iCloud может запретить TCC (PermissionError) —
  тогда работаем локально и показываем баннер с кнопкой в «Конфиденциальность и безопасность».
- Bundle id — из переменной `BUNDLE_ID` (по умолчанию нейтральный `io.github.portfoliocheck`).
- **Windows и Linux**: `build.py` (PyInstaller `--onedir`; Windows — zip и .ico через Pillow, Linux — tar.gz с
  `.desktop`, иконкой и `install.sh`). Секреты — `keyring` (`secrets_store.USE_KEYRING`): Credential Manager /
  Secret Service; нет бэкенда — файл 0600. Папки — `%APPDATA%` / `$XDG_DATA_HOME`. Новый модуль добавляй **в обе**
  сборки (`build_mac.sh` и `build.py`) — `tests/test_platform.py` это проверяет. Бандл сертификатов собирается без консольного `openssl`
  (`settings_dialog.server_chain`).
- **CI** (`.github/workflows/ci.yml`): тесты на трёх ОС, `check_private.sh`, сборки macOS / Windows / Linux и запуск
  собранной программы с `--demo`. Linux собирается на ubuntu-22.04 — старее glibc, шире совместимость.
- **Релиз** (`release.yml`): Releases → Publish с тегом `vX.Y` → dmg, zip и tar.gz прикладываются сами.
  Версия — `diagnostics.APP_VERSION`.
