#!/usr/bin/env bash
# Сборка PortfolioCheck.app для macOS. Запуск: ./build_mac.sh (из папки проекта, venv активировать не нужно).
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo "СБОРКА ПРЕРВАНА на строке $LINENO: $BASH_COMMAND" >&2' ERR

# 1. Интерпретатор: берём Homebrew-питон 3.12 явно, не полагаясь на то, куда указывает python3.
PY=""
for cand in python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3.13 python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
[ -n "$PY" ] || { echo "Не найден python3.12 (или 3.11/3.13). Поставь: brew install python@3.12"; exit 1; }
echo "Python: $PY ($("$PY" --version))"

# 2. Отдельное чистое окружение для сборки — чтобы в .app не попало лишнее из .venv разработки.
rm -rf .venv-build build dist
"$PY" -m venv .venv-build
source .venv-build/bin/activate
pip install -q --upgrade pip
bash ./setup_env.sh          # через bash — не зависит от бита исполняемости после копирования файлов

# 3. Иконка: SVG → iconset → icns (iconutil есть в macOS). Версия — из кода, пока venv ещё активен.
python scripts/make_icon.py
iconutil -c icns icon.iconset -o PortfolioCheck.icns
VERSION="${VERSION:-$(PYTHONPATH=src python -c "import diagnostics; print(diagnostics.APP_VERSION)")}"

# 4. Сборка.
pyinstaller --noconfirm --windowed --name PortfolioCheck \
  --icon PortfolioCheck.icns \
  --paths src \
  --add-data "src/icon.svg:." \
  --collect-all tinkoff \
  --hidden-import settings_dialog \
  --hidden-import forecast \
  --hidden-import activity \
  --hidden-import charts \
  --hidden-import macro \
  --hidden-import paths \
  --hidden-import history \
  --hidden-import news \
  --hidden-import screener \
  --hidden-import diagnostics \
  --hidden-import ai \
  --hidden-import secrets_store \
  --hidden-import strategy \
  --hidden-import config_migrate \
  --hidden-import demo \
  --hidden-import ui_common \
  --hidden-import widgets \
  --hidden-import osutil \
  --hidden-import payouts \
  --hidden-import attribution \
  --hidden-import export \
  --hidden-import risk \
  --hidden-import alerts \
  --hidden-import factors \
  --hidden-import scenarios \
  --hidden-import consensus \
  --hidden-import workers \
  --collect-submodules screens \
  --hidden-import wizard \
  --add-data "docs:docs" \
  --hidden-import net \
  --hidden-import style \
  --hidden-import jsoncache \
  --hidden-import truststore \
  --hidden-import certifi \
  --hidden-import portfolio_check \
  --add-data "src/config.yaml:." \
  --add-data "src/demo_snapshot.json:." \
  --osx-bundle-identifier "${BUNDLE_ID:-io.github.portfoliocheck}" \
  src/app.py

# Служебные приложения Qt (Assistant, Designer, Linguist…) в бандле не нужны и ломают codesign
# ("Too many levels of symbolic links") — вычищаем и подписываем сами (ad-hoc, для запуска на своих Mac).
find dist/PortfolioCheck.app/Contents -type d -name "*__dot__app" -prune -exec rm -rf {} + 2>/dev/null || true
find dist/PortfolioCheck.app/Contents -type d \( -name "Assistant.app" -o -name "Designer.app" -o -name "Linguist.app" \) -prune -exec rm -rf {} + 2>/dev/null || true

# Info.plist: следовать системной теме (тёмная/светлая), человеческое имя, минимальная версия macOS
PLIST="dist/PortfolioCheck.app/Contents/Info.plist"
plutil -replace NSRequiresAquaSystemAppearance -bool false "$PLIST"
plutil -replace CFBundleDisplayName -string "PortfolioCheck" "$PLIST"
plutil -replace LSMinimumSystemVersion -string "12.0" "$PLIST"
# Описания доступа к папкам — чтобы macOS показывала штатный запрос, а не молча отказывала (TCC)
plutil -replace NSDocumentsFolderUsageDescription -string "Хранение настроек и снапшотов портфеля" "$PLIST"
plutil -replace NSDesktopFolderUsageDescription -string "Открытие и сохранение снапшотов" "$PLIST"
plutil -replace NSDownloadsFolderUsageDescription -string "Открытие снапшотов" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "Личный инструмент. Не является инвестиционной рекомендацией." "$PLIST"

# Подпись — строго ПОСЛЕ всех правок бандла: любое изменение файлов после codesign делает подпись
# недействительной, и Keychain отказывает приложению («make sure executable is signed with codesign util»).
# Идентичность подписи: самоподписанный сертификат из Keychain Access (см. README) — стабильная подпись между
# пересборками, и Keychain перестаёт спрашивать доступ после каждой сборки. Без него — ad-hoc («-»).
IDENTITY="${CODESIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  # grep без совпадений возвращает 1 — не даём set -e уронить скрипт
  IDENTITY="$( (security find-identity -v -p codesigning 2>/dev/null || true) | (grep -o '"PortfolioCheck[^"]*"' || true) | head -1 | tr -d '"')"
fi
if [ -z "$IDENTITY" ]; then
  IDENTITY="-"
  echo "Подпись: ad-hoc (сертификат PortfolioCheck не найден — Keychain будет спрашивать доступ после каждой сборки, см. README)"
else
  echo "Подпись: $IDENTITY"
fi
codesign --force --deep --sign "$IDENTITY" dist/PortfolioCheck.app
codesign --verify --deep --strict dist/PortfolioCheck.app && echo "Подпись: ок" || { echo "ВНИМАНИЕ: подпись бандла не проходит проверку"; exit 1; }

deactivate

# 5. DMG с ярлыком на /Applications — обычная установка «перетащи в папку».
DMG="dist/PortfolioCheck-${VERSION}.dmg"
STAGE="$(mktemp -d)"
cp -R dist/PortfolioCheck.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
# если прошлый образ ещё смонтирован (открывали в Finder) — размонтировать, иначе hdiutil откажет
for vol in /Volumes/PortfolioCheck*; do [ -d "$vol" ] && hdiutil detach "$vol" -force >/dev/null 2>&1 || true; done
set +e
HD_OUT=$(hdiutil create -volname "PortfolioCheck" -srcfolder "$STAGE" -ov -format UDZO "$DMG" 2>&1)
HD_RC=$?
if [ $HD_RC -ne 0 ] || [ ! -f "$DMG" ]; then
  echo "hdiutil не справился, пробую diskutil:"; echo "$HD_OUT" | grep -v -i deprecated
  diskutil image create fromfolder "$STAGE" --volumeName PortfolioCheck --format UDZO "$DMG" 2>&1 | grep -v -i deprecated || true
fi
set -e
rm -rf "$STAGE"
if [ -f "$DMG" ]; then echo "DMG: $DMG"; else echo "ВНИМАНИЕ: DMG не создан — приложение dist/PortfolioCheck.app при этом готово, его можно просто перетащить в Applications"; fi

# 6. Черновики PyInstaller больше не нужны (в build/ лежит его внутренний архив .pkg — это НЕ установщик).
rm -rf build PortfolioCheck.spec icon.iconset

echo
echo "Готово:"
echo "  приложение: $(pwd)/dist/PortfolioCheck.app"
[ -f "$DMG" ] && echo "  образ:      $(pwd)/$DMG   (открыть → перетащить в Applications)"
echo "Проверка из терминала: ./dist/PortfolioCheck.app/Contents/MacOS/PortfolioCheck"
