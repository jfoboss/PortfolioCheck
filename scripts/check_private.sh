#!/usr/bin/env bash
# Проверка перед публикацией: нет ли в исходниках личных данных.
# Ищет ISIN, причины заморозок и чёрного списка из ТВОЕГО рабочего конфига, а также типовые утечки.
set -uo pipefail
cd "$(dirname "$0")/.."
FILES=$(git ls-files 2>/dev/null || find . -type f \( -name "*.py" -o -name "*.sh" -o -name "*.md" -o -name "*.yaml" -o -name "*.json" \) -not -path "./.venv/*")
found=0
check() {  # $1 — описание, $2 — шаблон (grep -E)
  hits=$(echo "$FILES" | xargs grep -n -E "$2" 2>/dev/null | grep -v "scripts/check_private.sh" | grep -v -E "DEMO|XXXX")
  if [ -n "$hits" ]; then echo "⚠ $1:"; echo "$hits" | head -10; echo; found=1; fi
}
check "токены T-Invest"            't\.[A-Za-z0-9_-]{40,}'
check "ключи API"                  'sk-[A-Za-z0-9-]{20,}|sk-or-[A-Za-z0-9-]{20,}'
check "домашние пути"              '/Users/[a-z]+/'
check "реальные ISIN (кроме демо)" 'RU000A[0-9][0-9A-Z]{5}'
# личное из рабочего конфига
CFG="$HOME/Library/Application Support/PortfolioCheck/config.yaml"
[ -f "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PortfolioCheck/config.yaml" ] && CFG="$HOME/Library/Mobile Documents/com~apple~CloudDocs/PortfolioCheck/config.yaml"
if [ -f "$CFG" ] && command -v python3 >/dev/null; then
  python3 - "$CFG" <<'PY' > /tmp/pc_private_patterns.txt
import sys, yaml
c = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
out = set()
for sec in ((c.get("rules") or {}).get("frozen") or {}, (c.get("screener") or {}).get("blacklist") or {}):
    for t, why in (sec.items() if isinstance(sec, dict) else []):
        if why and len(str(why)) > 12:
            out.add(str(why)[:40])
for t in (c.get("instruments") or {}):
    if str(t).startswith("RU000A"):
        out.add(str(t))
print("\n".join(sorted(out)))
PY
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    hits=$(echo "$FILES" | xargs grep -n -F "$pat" 2>/dev/null | grep -v "scripts/check_private.sh")
    if [ -n "$hits" ]; then echo "⚠ из твоего конфига найдено в исходниках: «$pat»"; echo "$hits" | head -5; echo; found=1; fi
  done < /tmp/pc_private_patterns.txt
fi
if [ $found -eq 0 ]; then echo "✓ личных данных в исходниках не найдено"; else echo "Исправь найденное перед публикацией."; exit 1; fi
