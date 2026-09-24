# Архитектура

## Слои

Все модули и ресурсы программы (`config.yaml`, `demo_snapshot.json`, `icon.svg`) — в `src/`; в корне репозитория —
README, скрипты сборки (`build_mac.sh`, `build.py`, `scripts/`), `docs/`, `tests/`.

```
app.py  ── каркас главного окна: сборка UI, меню, конвейер загрузки, действия, main()
  ├─ screens/            ── экраны как миксины MainWindow: history (история, тепловая карта), news (лента, ИИ-сводка),
  │                         summaries (ИИ-сводки экранов), ideas (скринер), privacy («глазик»), overview (отрисовка)
  ├─ workers.py          ── фоновые загрузки (QThread)
  ├─ widgets.py          ── BusySpinner, BadgeDelegate
  ├─ ui_common.py        ── имя приложения, цвета подсветки, get_token, ensure_config
  ├─ settings_dialog.py  ── окно настроек (11 вкладок), каталог моделей, проверка RSS
  ├─ wizard.py           ── мастер первого запуска
  ├─ forecast.py         ── экран прогноза «Стратегия» (виджет + модель расчёта)
  ├─ charts.py           ── графики на QPainter: Line/Pie/Bar/RangeBar/DivergingBar/Treemap
  └─ diagnostics.py      ── логирование, окно «О программе» (Справка), проверка подключений

Ядро (без Qt, тестируется отдельно):
  portfolio_check.py  ── T-Invest: позиции, курсы, классы, правила, отчёт, доходности
  strategy.py         ── шаблоны стратегий, автоклассификация, импорт/экспорт YAML, промпт ChatGPT
  history.py          ── дневные свечи, изменения за периоды, CAGR, восстановленная история
  news.py             ── RSS/Atom, каталог источников, привязка новостей к бумагам
  screener.py         ── дивидендный скринер по фундаменталу T-Invest
  consensus.py        ── консенсус-прогнозы аналитиков (GetForecastBy), кэш forecast_<uid>.json на сутки
  scenarios.py        ── сценарии: двухфакторные беты (индекс, доллар) и оценка изменения стоимости
  factors.py          ── ряды факторов: IMOEX (MOEX ISS) и USD/RUB (T-Invest, запасной — ЦБ), кэш candles_IMOEX / candles_USDRUB
  alerts.py           ── оповещения-условия: проверка после обновлений, однократность (cache/alerts_state.json)
  risk.py             ── риск по свечам: волатильность, просадка, эффективное N, вклад в риск, корреляции
  export.py           ── CSV под русский Excel: таблицы окна и история стоимости
  attribution.py      ── откуда изменение стоимости: переоценка / покупки нетто между снапшотами
  payouts.py          ── календарь выплат: прошлые/объявленные из GetDividends, «ожидается» по прошлому году, поток
  ai.py               ── провайдеры LLM, сводки, кэш ответов, каталог OpenRouter, разбор 429
  macro.py            ── инфляция и ставка с сайта ЦБ РФ; календарь решений по ставке (cache/macro_calendar.json)
  net.py              ── сеть к сайтам (RSS, ISS, ЦБ, ИИ): fetch, get_json, opener с прокси; TLS — корни ОС + Минцифры из бандла
  style.py            ── цвета, значки, тональность, маска сумм, текст ошибки (без Qt)
  jsoncache.py        ── JSON-кэши: read / атомарная write / fresh
  paths.py            ── где лежат данные (локально / iCloud Drive), миграция; папка по ОС (macOS / Windows / Linux)
  osutil.py           ── различия ОС: платформа, подписи сочетаний клавиш, моноширинный шрифт, имя хранилища секретов
  config_migrate.py   ── config_version и явные миграции формата конфига
  demo.py             ── режим --demo: временная папка данных, демо-снапшот, секреты только в памяти
  secrets_store.py    ── Keychain через /usr/bin/security (macOS), keyring: Credential Manager (Windows), Secret Service (Linux)
```

`forecast.py` содержит и модель (`simulate`, `simulate_monthly`, `plan_status`, `required_contribution`,
`goal_year_absolute`), и виджет `ForecastTab` — модельные функции Qt не используют.

## Гостевой режим (первый запуск)

`app.main`: нет токена и ни одного снапшота → `demo.setup()` и `MainWindow.enter_guest_mode()` — демо-портфель,
на панели одна кнопка «Настроить…». По ней `switch_to_real_profile()` без перезапуска создаёт окно на настоящем
конфиге (новому пользователю сразу открывается мастер), секреты снова читаются из хранилища, временная папка
демо удаляется. Лог в гостевом режиме пишется в настоящий профиль. `--demo` — то же демо, но без гостевых кнопок
(для скриншотов).

## Конвейер загрузки

1. **Старт с диска** (`MainWindow.load_from_disk`): последний снапшот, кэш свечей, новости, ИИ-сводки, кэш
   скринера, переоценка по выбранной базе — всё сразу, без сети.
2. **Обновление** (если снапшот старше `refresh.interval_min` или по кнопке): `LoadWorker` (портфель, доход,
   макро) → `on_loaded` (автоклассификация новых бумаг, снапшот, отрисовка) → `HistoryWorker` (свечи позиций) →
   `NewsWorker` → `ScreenerWorker` → `IdeasEnrichWorker` (свечи кандидатов, изменение за месяц) → `AIWorker`
   (сводка новостей) → `ExecWorker` (сводки экранов одним запросом).
3. `LongHistoryWorker` — свечи за 3/5 лет для CAGR, только если выбранная база длиннее имеющегося кэша.
4. `FairPriceWorker` — оценки заблокированных бумаг (внебиржа, эталонный тикер).

## Данные на диске

Корень — `paths.data_dir()`. Локальная папка (`paths.LOCAL_DIR`) — по ОС: macOS `~/Library/Application Support/PortfolioCheck/`,
Windows `%APPDATA%\PortfolioCheck\`, Linux `$XDG_DATA_HOME/PortfolioCheck/` (обычно `~/.local/share`). Вместо неё
можно выбрать iCloud Drive (только macOS) или любую папку; выбор — в `storage.json` локальной папки.

| путь | что | кто пишет |
|---|---|---|
| `config.yaml` | стратегия, классы, правила, настройки, параметры прогноза | настройки, мастер, импорт |
| `config.yaml.v<N>.bak` | копия конфига версии N перед миграцией | `config_migrate.load_and_migrate` |
| `snapshots/YYYY-MM-DD.json` | позиции на дату (история стоимости) | `on_loaded` |
| `cache/candles_<figi или uid>.json` | дневные свечи `[(дата, close)]` | `history.fetch_candles` |
| `cache/news.json` | лента | `news.save_cache` |
| `cache/alerts_state.json` | какие условия оповещений выполнены сейчас (чтобы не повторять) | `alerts.new_hits` |
| `cache/candles_IMOEX.json`, `cache/candles_USDRUB.json` | ряды факторов для сценариев (раз в сутки) | `factors.fetch_all` |
| `cache/macro_calendar.json` | календарь ЦБ по ключевой ставке (раз в неделю, вместе с факторами) | `macro.fetch_calendar` |
| `cache/payouts.json` | выплаты для календаря (прошлые, объявленные, ожидаемые) | `payouts.save` |
| `cache/candidate_payouts.json` | ближайшие выплаты кандидатов скринера (GetDividends при догрузке кандидатов) | `payouts.save_candidates` |
| `cache/screener.json` | результат скринера (`source: tinvest2`) | `screener.screen` |
| `cache/ai_responses.json` | кэш ответов ИИ с отпечатками | `ai.ResponseCache` |
| `cache/ai_summary.json`, `cache/exec_<экран>.json` | последние сводки для мгновенного показа | `ai.save_*` |
| `cache/ai_rate_block.json` | до какого времени не слать запросы к `:free` (дневной лимит) | `ai.set_rate_block` |
| `cache/ai_model_checks.json` | результаты кнопки «Проверить» по моделям | `ai.check_model` |
| `cache/openrouter_models.json` | каталог моделей OpenRouter (сутки) | `ai.fetch_openrouter_models` |
| `logs/` | лог приложения | `diagnostics.setup_logging` |
| `ca-bundle.pem` | корни для gRPC (grpcio + Минцифры) | Настройки → Доступ → «Собрать бандл» (`settings_dialog.build_ca_bundle`) |

Секреты — только в системном хранилище (сервисы в `secrets_store.py`), в файлы не пишутся — кроме аварийного файла
0600 в локальной папке, если хранилище недоступно (например, Linux без Secret Service).
Размеры окна и разделителей — `QSettings("PortfolioCheck", "PortfolioCheck")`.

## Ключевые форматы

- **Конфиг**: `config_version` (первой строкой; нет — значит 0; сейчас 2), разделы `alerts`, `targets`, `rules`, `instruments`, `screener`, `news`, `ai`, `forecast`, `refresh`,
  `fx_fallback`, `account`, служебные `setup_done`, `strategy_preset`, `auto_classify`, `auto_classified`.
  Схему «стратегии» (переносимой части) задают `strategy.STRATEGY_KEYS` и `strategy.validate_strategy`.
  Окно читает конфиг через `config_migrate.load_and_migrate`: старый конфиг мигрирует по шагам `MIGRATIONS`,
  сохраняется, рядом остаётся `.bak`. Конфиг новее программы не переписывается. Код читает конфиг как есть —
  дефолты к данным пользователя не домешиваются (`screener.blacklist` — только из конфига, без
  `blacklist_customized`, который убран в v1).
- **Position** (`portfolio_check.Position`): тикер, имя, figi/isin, тип, валюта, класс, количество, средняя и
  текущая цена, стоимость в ₽, выплаты за 12 мес (`div_12m`), флаги блокировки и оценки справедливой цены.

## Экраны

- **Обзор** — KPI, доли против коридоров, «докупить/продать до границы», сигналы, новости, дневная карта.
- **Портфель** — позиции, пироги (валюты/компании/отрасли), заблокированные бумаги с тремя оценками.
- **Календарь** — отсечки и выплаты на 12 месяцев, поток по месяцам, отсечки кандидатов (`screens/payouts.py`).
- **Рынок** — тепловая карта с группами-отраслями, лента новостей с ИИ-сводкой.
- **Идеи** — скринер (отдельной вкладкой: таблице нужна вся ширина).
- **Стратегия** — прогноз, «Где я сейчас» (план/факт), история стоимости, текстовый отчёт.
- **О программе** — отдельное окно (`diagnostics.AboutWindow`, меню «Справка» / меню приложения на macOS): версия,
  лог, проверка подключений, копирование диагностики.

Меню: Файл (мастер, снапшоты, настройки, выход) · Вид (обновить, глазик, ИИ-сводки, экраны ⌘1…) · Окно · Справка.
Роли macOS: `PreferencesRole`, `AboutRole`, `QuitRole` — Qt переносит эти пункты в меню приложения. Вкладка
запоминается по имени, а не по индексу.

## Платформы и сборка

| | macOS | Windows | Linux |
|---|---|---|---|
| сборка | `build_mac.sh` → `.app` + `.dmg` (ad-hoc codesign) | `build.py` → onedir + zip | `build.py` → onedir + tar.gz, `.desktop`, `install.sh` |
| секреты | `/usr/bin/security` | keyring → Credential Manager | keyring → Secret Service |
| папка данных | Application Support, iCloud | `%APPDATA%` | `$XDG_DATA_HOME` |
| особенности | меню приложения (роли Qt), TCC для iCloud | SmartScreen без подписи | нужны `libxcb-cursor0` и др. для Qt; сборка на ubuntu-22.04 (glibc 2.35) |

Различия ОС в коде — только `osutil.py` и `paths.py`. CI (`ci.yml`) гоняет тесты на трёх ОС, собирает все три
варианта и запускает собранную программу с `--demo` на 20 секунд; `release.yml` прикладывает сборки к релизу.
