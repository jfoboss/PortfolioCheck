---
paths:
  - "portfolio_check.py"
  - "history.py"
  - "screener.py"
  - "macro.py"
  - "strategy.py"
---
# Данные: T-Invest API, классы, расчёты

- **SDK** — `tinkoff-investments` (gRPC). Импорт лениво через `pc._sdk()`; перед клиентом — `pc._setup_grpc_roots()`
  (корни из CA-бандла: корни grpcio + сертификаты Минцифры, собирает `make_ca_bundle.sh` / кнопка в настройках).
- **Деньги — `Decimal`** внутри ядра; `float` — только для графиков и статистики.
- **Классы** (`CLASS_LABELS`): `gold`, `real_estate_wh`, `real_estate_other`, `equity_core`, `equity_spec`, `ballast`,
  `foreign`, `cash`, `unclassified`; в коридорах дополнительно `real_estate` — весь блок ЗПИФ.
  Автоклассификация — `strategy.auto_class` (консервативная: чего не знает — оставляет без класса).
- **Золото** (`GLDRUB_TOM`) — инструмент типа `currency`; дневные свечи по нему API отдаёт не всегда.
- **Свечи**: `history.fetch_candles` кэширует ряд по `figi` и догружает хвост; кандидаты скринера — по `uid`
  (`by_uid=True`). Длинная история (3/5 лет) грузится по запросу и считается из кэша (`history.cagr`).
- **Скринер**: вселенная — акции TQBR, фундаментал `GetAssetFundamentals`; кэш `cache/screener.json` с версией
  `source: tinvest2` (без `uid` кэш считается устаревшим). Чёрный список по умолчанию пуст — только из конфига.
- **Доходность новых денег** = Σ(целевая доля класса × дивдоходность класса), золото даёт 0% денежного потока
  (`new_money_yield`). **Переоценка** — рост цен по классам, взвешенный по целевым долям; классы без свечей
  исключаются (`price_growth_target_weighted`). Доли — середины коридоров, нормированные к 100%.
- **Прогноз** (`forecast.py`): помесячная симуляция; год цели считается до 50 лет независимо от горизонта
  (`goal_year_absolute`); требуемый взнос — бинарным поиском (`required_contribution`).
- **Экспорт стратегии** (`strategy.export_strategy`) никогда не включает `account`, токены и личные суммы
  прогноза (`PERSONAL_FORECAST_KEYS`).
