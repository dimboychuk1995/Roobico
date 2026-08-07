# Скриншоты для App Store

Сырые скриншоты + оформление в витринные карточки нужных Apple размеров
(iPhone 6.9″ 1320×2868, iPad Pro 13″ 2064×2752).

## Как снять сырые скриншоты

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
