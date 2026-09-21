#!/usr/bin/env bash
# Установка зависимостей. Запускать внутри активированного venv: source .venv/bin/activate && ./setup_env.sh
#
# Почему так сложно: пакет tinkoff-investments с PyPI удалён, а его преемник t-tech-investments
# на карантине у PyPI. Рабочий источник — GitHub. Ставим с --no-deps, потому что SDK требует
# служебный пакет "tinkoff", которого на PyPI тоже нет; без него всё работает (это namespace).
set -euo pipefail

pip install -q --upgrade pip
pip install -q --no-deps "git+https://github.com/RussianInvestments/invest-python.git"
pip install -q -r requirements.txt

python -c "from tinkoff.invest import Client; import PySide6, keyring, yaml; print('Окружение готово.')"
