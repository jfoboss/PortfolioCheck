# Архитектура

## Слои

```
app.py  ── главное окно, 4 экрана + «О программе», воркеры (QThread), конвейер загрузки
  ├─ settings_dialog.py  ── окно настроек (11 вкладок), каталог моделей, проверка RSS
  ├─ wizard.py           ── мастер первого запуска
  ├─ forecast.py         ── экран прогноза «Стратегия» (виджет + модель расчёта)
  ├─ charts.py           ── графики на QPainter: Line/Pie/Bar/RangeBar/DivergingBar/Treemap
  └─ diagnostics.py      ── логирование, вкладка «О программе», проверка подключений

Ядро (без Qt, тестируется отдельно):
  portfolio_check.py  ── T-Invest: позиции, курсы, классы, правила, отчёт, доходности
  strategy.py         ── шаблоны стратегий, автоклассификация, импорт/экспорт YAML, промпт ChatGPT
  history.py          ── дневные свечи, изменения за периоды, CAGR, восстановленная история
  news.py             ── RSS/Atom, каталог источников, привязка новостей к бумагам
  screener.py         ── дивидендный скринер по фундаменталу T-Invest
  ai.py               ── провайдеры LLM, сводки, кэш ответов, каталог OpenRouter, разбор 429
  macro.py            ── инфляция и ставка с сайта ЦБ РФ
  paths.py            ── где лежат данные (локально / iCloud Drive), миграция
  secrets_store.py    ── Keychain через /usr/bin/security
```

`forecast.py` содержит и модель (`simulate`, `simulate_monthly`, `plan_status`, `required_contribution`,
`goal_year_absolute`), и виджет `ForecastTab` — модельные функции Qt не используют.

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

Корень — `paths.data_dir()`: `~/Library/Application Support/PortfolioCheck/` или
`~/Library/Mobile Documents/com~apple~CloudDocs/PortfolioCheck/` (выбор — в `storage.json` локальной папки).

| путь | что | кто пишет |
|---|---|---|
| `config.yaml` | стратегия, классы, правила, настройки, параметры прогноза | настройки, мастер, импорт |
| `snapshots/YYYY-MM-DD.json` | позиции на дату (история стоимости) | `on_loaded` |
| `cache/candles_<figi или uid>.json` | дневные свечи `[(дата, close)]` | `history.fetch_candles` |
| `cache/news.json` | лента | `news.save_cache` |
| `cache/screener.json` | результат скринера (`source: tinvest2`) | `screener.screen` |
| `cache/ai_responses.json` | кэш ответов ИИ с отпечатками | `ai.ResponseCache` |
| `cache/ai_summary.json`, `cache/exec_<экран>.json` | последние сводки для мгновенного показа | `ai.save_*` |
| `cache/openrouter_models.json` | каталог моделей OpenRouter (сутки) | `ai.fetch_openrouter_models` |
| `logs/` | лог приложения | `diagnostics.setup_logging` |
| `ca-bundle.pem` | корни для gRPC (grpcio + Минцифры) | `make_ca_bundle.sh`, настройки |

Секреты — только в Keychain (сервисы в `secrets_store.py`), в файлы не пишутся (кроме аварийного файла 0600).
Размеры окна и разделителей — `QSettings("PortfolioCheck", "PortfolioCheck")`.

## Ключевые форматы

- **Конфиг**: разделы `targets`, `rules`, `instruments`, `screener`, `news`, `ai`, `forecast`, `refresh`,
  `fx_fallback`, `account`, служебные `setup_done`, `strategy_preset`, `auto_classify`, `auto_classified`.
  Схему «стратегии» (переносимой части) задают `strategy.STRATEGY_KEYS` и `strategy.validate_strategy`.
- **Position** (`portfolio_check.Position`): тикер, имя, figi/isin, тип, валюта, класс, количество, средняя и
  текущая цена, стоимость в ₽, выплаты за 12 мес (`div_12m`), флаги блокировки и оценки справедливой цены.

## Экраны

- **Обзор** — KPI, доли против коридоров, «докупить/продать до границы», сигналы, новости, дневная карта.
- **Портфель** — позиции, пироги (валюты/компании/отрасли), заблокированные бумаги с тремя оценками.
- **Рынок** — тепловая карта с группами-отраслями, лента новостей с ИИ-сводкой, скринер «Идеи».
- **Стратегия** — прогноз, «Где я сейчас» (план/факт), история стоимости, текстовый отчёт.
- **О программе** — версия, лог, проверка подключений, копирование диагностики.
