#!/usr/bin/env bash
# Установка «под ключ» на чистом Mac: инструменты → Python 3.12 → сборка .app и .dmg → открыть папку.
# Запуск:  bash bootstrap_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/4 Command Line Tools"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install || true
  echo "Подтверди установку в появившемся окне и, когда закончится, запусти этот скрипт снова."
  exit 0
fi

echo "== 2/4 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# на Apple Silicon brew живёт в /opt/homebrew и не всегда в PATH новой сессии
for p in /opt/homebrew/bin /usr/local/bin; do case ":$PATH:" in *":$p:"*) ;; *) export PATH="$p:$PATH";; esac; done

echo "== 3/4 Python 3.12"
command -v python3.12 >/dev/null 2>&1 || brew install python@3.12

echo "== 4/4 Сборка"
chmod +x build_mac.sh setup_env.sh make_ca_bundle.sh 2>/dev/null || true
./build_mac.sh
open dist/
echo
echo "Дальше: открыть dist/PortfolioCheck-*.dmg → перетащить в Applications → первый запуск правым кликом «Открыть»."
echo "В приложении: ⌘, → Доступ → задать read-only токен и нажать «Собрать бандл». Всё остальное само."
