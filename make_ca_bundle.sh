#!/usr/bin/env bash
# Собирает минимальный PEM-бандл для gRPC: корни из grpcio + переданные сертификаты.
# T-Invest API подписан корнем Минцифры (Russian Trusted Root CA), которого у grpcio нет.
# Обычно не нужен: бандл собирает сама программа (Настройки → Доступ → «Собрать бандл», на любой ОС). Скрипт —
# для ручной сборки на macOS/Linux, например с корпоративным корнем за MITM-прокси.
#
#   ./make_ca_bundle.sh /tmp/russian_root.pem            # обычный случай
#   ./make_ca_bundle.sh --with-keychain corp_root.pem    # + все корни из Keychain (корпоративный MITM-прокси)
set -euo pipefail

# папка данных — та же, что у программы (paths.LOCAL_DIR: Application Support на macOS, ~/.local/share на Linux)
OUT_DIR="$(cd "$(dirname "$0")/src" && python -c 'import paths; print(paths.LOCAL_DIR)')"
OUT="$OUT_DIR/ca-bundle.pem"
mkdir -p "$OUT_DIR"

WITH_KEYCHAIN=0
if [ "${1:-}" = "--with-keychain" ]; then WITH_KEYCHAIN=1; shift; fi
[ $# -ge 1 ] || { echo "Укажи хотя бы один PEM с корнем, например /tmp/russian_root.pem"; exit 1; }

GRPC_ROOTS=$(python -c "import grpc, os; print(os.path.join(os.path.dirname(grpc.__file__), '_cython', '_credentials', 'roots.pem'))")
[ -f "$GRPC_ROOTS" ] || { echo "Не найден roots.pem у grpcio: $GRPC_ROOTS"; exit 1; }

TMP=$(mktemp)
cat "$GRPC_ROOTS" > "$TMP"
echo >> "$TMP"   # roots.pem у grpcio без завершающего \n — без этого следующий сертификат склеится и не распарсится

for pem in "$@"; do
  [ -f "$pem" ] || { echo "Нет файла: $pem"; exit 1; }
  # нормализуем: только блоки сертификатов, без мусора вокруг
  awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/' "$pem" >> "$TMP"
  echo >> "$TMP"
  echo "Добавлен: $pem ($(grep -c 'BEGIN CERTIFICATE' "$pem") серт.)"
done

if [ $WITH_KEYCHAIN -eq 1 ]; then
  for kc in /Library/Keychains/System.keychain "$HOME/Library/Keychains/login.keychain-db"; do
    [ -e "$kc" ] && security find-certificate -a -p "$kc" >> "$TMP" 2>/dev/null || true
  done
  echo "Добавлены корни из Keychain"
fi

mv "$TMP" "$OUT"
# Самопроверка: openssl должен прочитать бандл целиком (один битый сертификат — и gRPC остановится на нём)
N_TOTAL=$(grep -c 'BEGIN CERTIFICATE' "$OUT")
if N_PARSED=$(openssl crl2pkcs7 -nocrl -certfile "$OUT" 2>/dev/null | openssl pkcs7 -print_certs -noout 2>/dev/null | grep -c '^subject='); then
  echo "Бандл: $OUT — сертификатов $N_TOTAL, распарсилось $N_PARSED"
  [ "$N_TOTAL" -eq "$N_PARSED" ] || echo "ВНИМАНИЕ: часть сертификатов не парсится"
else
  echo "ВНИМАНИЕ: openssl не смог прочитать бандл целиком"
fi
openssl crl2pkcs7 -nocrl -certfile "$OUT" 2>/dev/null | openssl pkcs7 -print_certs -noout 2>/dev/null | grep -q "Russian Trusted Root CA" \
  && echo "Russian Trusted Root CA в бандле: есть" || echo "Russian Trusted Root CA в бандле: НЕТ"
echo "Проверка: python src/portfolio_check.py"
