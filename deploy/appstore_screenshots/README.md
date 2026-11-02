# Скриншоты для App Store

Сырые скриншоты + оформление в витринные карточки нужных Apple размеров
(iPhone 6.9″ 1320×2868, iPad Pro 13″ 2064×2752).

## Как снять сырые скриншоты автоматически (веб-рендер приложения)

Так сняты все текущие карточки — без телефона, из react-native-web:

```powershell
# 1. Flask на :5000 (dev-база) — в отдельном окне: python run.py
# 2. Демо-записи календаря в dev-базе тестового тенанта (идемпотентно,
#    прод не трогает): неделя 3 августа 2026, где есть WO и таймеры
python deploy\appstore_screenshots\seed_calendar_dev.py
# 3. Веб-сборка приложения
cd mobile; npx expo export --platform web --output-dir ..\deploy\appstore_screenshots\dist; cd ..
# 4. Same-origin прокси (127.0.0.1:7777 -> статика + Flask) — в отдельном окне
cd deploy\appstore_screenshots; node proxy.mjs .\dist
# 5. Сырые кадры: raw/iphone, raw/ipad (+ -dark)
node capture.mjs; node capture.mjs dark
```

Календарь снимается через `/calendar?view=day&date=2026-08-04` — экран
понимает эти параметры. Playwright/sharp ставятся `npm install` в этой папке.

## Как снять сырые скриншоты с устройства

1. Поставить свежий билд из TestFlight на iPhone и iPad.
2. Залогиниться в тестовый тенант (owner@roobico-test.com), светлая тема.
3. Снять по 6 экранов на каждом устройстве (кнопки громкости+power):
   Dashboard, деталка Work Order, редактор WO (лейборы+запчасти),
   Parts (или заказ с AI-сканом инвойса), скан инвойса, Calendar (день).
4. Сложить PNG в `raw/iphone/` и `raw/ipad/` с именами из `captions.json`
   (01-dashboard.png … 06-calendar.png).

## Как собрать карточки

```powershell
cd deploy\appstore_screenshots
npm install     # один раз
node make_screenshots.mjs
```

Готовые файлы появятся в `out/` — их загружать в App Store Connect
(версия → App Store → скриншоты). Подписи/набор экранов меняются в
`captions.json`.

`raw/` и `out/` в git не коммитим (см. .gitignore).
