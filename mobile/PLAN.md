# Roobico Mobile — план до паритета с вебом

Expo SDK 54 (ровно под Expo Go пользователя; поднимем, когда обновится стор),
TypeScript, expo-router, `mobile/` монорепы. Бэкенд — тот же Flask:
существующие JSON-API + тонкие `/api/mobile/*` там, где веб рендерит HTML.

## Принципы

- **Бэкенд не дублируем**: mobile-эндпоинты — тонкие обёртки над сервисным
  слоем (`services/`), как уже сделано для списков. Каждый новый эндпоинт —
  с тестом в `tests/test_mobile_api.py`.
- **Права**: те же `permission_required`, что и в вебе; UI прячет действия
  по `session.permissions`.
- **Один список — один компонент**: `list-screen.tsx` переиспользуется везде.
- Деньги/даты форматируются как в вебе (UTC в базе, таймзона магазина).

## Статус

**v0, v1 и v2 готовы** (2026-07-03): логин/магазины, дашборд, все списки и
деталки (WO/customer/unit/vendor/part+history), платежи (запись/удаление/
mark paid/unpaid), CRUD клиентов/юнитов (VIN-декодер)/вендоров, вкладки
Payments/Estimates, **полный WO-редактор** (создание/правка: клиент/юнит,
лейборы, поиск запчастей с автоценой по прайсинг-матрице, пресеты,
issue description + AI-полировка, серверный расчёт тоталов+налога,
списание/возврат склада), удаление WO, отправка на утверждение клиенту.

**Из v3 готово**: parts orders полный цикл (список — вкладка Orders в Parts,
деталка, создание с поиском запчастей, receive/unreceive с движением склада,
оплата), календарь (неделя по дням, создание записи: клиент/юнит/механик/время).

**v3 готово**: календарь в Google-стиле (лента недели, таймлайн дня с
часовой сеткой и цветными блоками, линия «сейчас», создание тапом/FAB,
деталка со сменой статуса и удалением), отчёты (все 7 standard-отчётов:
generic-рендер summary+таблица с пресетами дат), email WO (PDF письмом),
вложения с камеры/галереи (общий блок на WO и юните, удаление лонг-тапом).

**Из v4 готово**: AI-скан инвойса камерой/из галереи (заполняет вендора и
позиции заказа), AI-распознавание бумажного WO камерой (лейборы + запчасти,
топ-кандидат из каталога, нераспознанные — one-time строки), глобальный
поиск (в More: результаты по всем сущностям с переходом в деталки).

**VIN-скан готов** (2026-09-01): кнопка «Scan VIN — barcode or text» на
экране нового WO (и иконка на форме юнита) — expo-camera читает VIN-баркод
(Code 39/128, DataMatrix, QR, PDF417) живьём, либо фото VIN-текста уходит
в OCR (`POST /api/mobile/vin/scan-image`, OpenAI vision). Резолв —
`POST /api/mobile/vin/resolve`: юнит найден → клиент+юнит подставляются
сразу; VIN на нескольких компаниях → выбор; юнита нет → автосоздание под
системным клиентом «NEW Customer» (is_system, не деактивируется; офис потом
переносит WO кнопкой Transfer). Тесты — `tests/test_vin_scan.py`.

**Push-уведомления готовы** (2026-08-05): офисным ролям приходит пуш, когда
механик берёт WO в работу и когда помечает Done (только WO-уровень, не
таймеры строк). Токены — master.push_tokens (регистрация после логина через
`POST /api/mobile/push-token`, снятие при logout), отправка — Expo Push API
в фоне (`app/utils/push_notifications.py`), события —
`work_orders/services/push_events.py` (старт таймера + сохранения механика
в вебе и mobile). Тап по пушу открывает WO (с переключением магазина).
В Expo Go remote push не работает (SDK 53+) — проверять в TestFlight-сборке.

Осталось: AVIR (годовая инспекция), chart в отчётах,
labor-scope авторизация, выбор кандидатов при AI-скане (сейчас топ-1).

## v0 (готово)

- [x] Логин (cookie-сессия + CSRF), настройка сервера, auth-гард
- [x] Табы: Dashboard / Work Orders / Customers / Parts / More
- [x] Списки WO, Customers, Parts, Vendors: поиск, пагинация, refresh
- [x] Dashboard: метрики периода с пресетами дат
- [x] Settings: переключение магазина, аккаунт, logout
- [x] Бэкенд: `/api/mobile/{login,session,logout,active-shop,work_orders,customers,vendors,parts}` + 7 тестов

## v1 — Просмотр и деньги (WO details, платежи, детали сущностей)

Цель: из приложения можно открыть любой WO/клиента/юнит/запчасть,
посмотреть всё и принять оплату.

**Экраны:**
- [ ] WO details (read): шапка (клиент/юнит/статус/даты), лейборы с
  запчастями, тоталы (labor/parts/tax/misc/core/grand), баланс
- [ ] Платежи WO: список, записать платёж (сумма/метод/заметка/дата),
  удалить платёж, mark paid → флоу как в вебе (баланс, overpayment)
- [ ] Список Payments и Estimates как отдельные вкладки/фильтры в Work Orders
- [ ] Customer details: карточка + вкладки (WOs, Units, Payments, Estimates),
  баланс, кнопка Portal Link
- [ ] Unit details (read): данные юнита, WO юнита, годовая инспекция
- [ ] Part details: карточка, история (`/parts/api/<id>/history`)
- [ ] Vendor details: карточка + part orders вендора
  (`/vendors/api/<id>/part-orders` — уже JSON)

**Бэкенд добавить** (тонкие, поверх сервисов):
- [ ] `GET /api/mobile/work_orders/<id>` — полная деталка WO
  (reuse pdf_contexts/common сервисы)
- [ ] `GET /api/mobile/customers/<id>` — деталка + units
- [ ] `GET /api/mobile/units/<id>` — деталка юнита + WOs
- [ ] Реюз существующих: `POST /work_orders/api/work_orders/<id>/payment`,
  `POST /work_orders/api/payments/<id>/delete`, `GET .../payments`,
  `GET /work_orders/api/estimates`, `/customers/api/balances`,
  `/portal/api/customers/<id>/send-link` — работают с cookie+CSRF как есть

**Инфраструктура:**
- [ ] Тост-система (успех/ошибка) вместо Alert
- [ ] Формы: числовой инпут денег, дата-пикер (@react-native-community/datetimepicker)
- [ ] Деталка открывается тапом по карточке списка (router push с id)

## v2 — Создание и редактирование

Цель: создать WO с запчастями в поле, завести клиента/юнит/вендора/запчасть.

- [ ] Create/Edit Customer (контакты, taxable, pricing scale, labor rate)
- [ ] Create/Edit Unit + VIN-декодер (`/work_orders/api/vin` готов)
- [ ] Create/Edit Vendor
- [ ] Create/Edit Part (без misc-конструктора в первой итерации)
- [ ] Create WO: выбор клиента/юнита, лейборы (описание/часы/rate),
  поиск запчастей (`/work_orders/api/parts/search` готов), пресеты
  (`/work_orders/api/presets` готов), тоталы с налогом — расчёт на бэке
- [ ] Edit WO + Save In Progress / Complete (reuse `/update` — уже JSON,
  диф инвентаря на бэке готов)
- [ ] Delete WO (возврат запчастей — готов)
- [ ] Estimates: создать, конвертировать в WO

**Бэкенд:** create/update у customers/vendors/parts уже JSON
(`/customers/api/*`, `/vendors/api/*`, `/parts/api/*`); create WO — форма
(form-data) → добавить JSON-вариант `POST /api/mobile/work_orders` поверх
той же логики (вынести парсинг формы из route в сервис).

## v3 — Календарь, отчёты, вложения, PDF

- [ ] Calendar: неделя списком по дням, создание/редактирование записи
  (все `/calendar/api/*` уже JSON: events, customers, units, presets,
  mechanics, statuses)
- [ ] Reports: standard-отчёты таблицей (`/reports/api/standard/<key>`
  готов), пресеты дат; график — react-native-svg (по данным API)
- [ ] Timecard/Salary — таблица
- [ ] Audit journal — список (нужен `GET /api/mobile/audit` или JSON-вариант)
- [ ] Вложения: просмотр списка/фото; загрузка с камеры/галереи
  (expo-image-picker) через существующий attachments API
- [ ] WO PDF: скачать/поделиться (expo-sharing), отправить клиенту email
  (`/send-email` готов), отправить авторизацию (`/send-authorization` готов)

## v4 — Продвинутое

- [ ] AI: сфотографировать бумажный WO → распознать
  (`/work_orders/api/parse-handwritten` готов — идеальный мобильный сценарий)
- [ ] AI-скан инвойса в parts order (`/parts/api/orders/parse-invoice` готов)
- [ ] Parts Orders полный цикл: создать заказ, receive/unreceive, оплата,
  cores (все `/parts/api/orders/*` уже JSON)
- [ ] Annual inspection (AVIR): создать с юнита, PDF
- [ ] Settings-подмножество: presets (CRUD готов JSON), users (просмотр),
  labor rates / tax (просмотр)
- [ ] Push-уведомления (expo-notifications + бэкенд-рассылка: новая
  авторизация от клиента, платёж)
- [ ] Global search (`/api/global-search` готов) — поиск из шапки

## Вне скоупа мобилки (остаётся в вебе)

Import/Export (не работает и в вебе), PDF-дизайнер, roles-матрица
редактирование, integrations (uAttend), billing/Stripe, admin panel,
организация/workflows.

## Технический долг мобилки

- [ ] Вернуться на актуальный SDK, когда Expo Go в сторе пользователя
  обновится (сейчас 54); либо перейти на development build (EAS) и
  отвязаться от Expo Go
- [ ] EAS build профили (dev/preview/production) + иконка/сплэш Roobico
- [ ] Обработка истечения сессии (401 → экран логина) — глобально в api.ts
- [ ] E2E-смоук (maestro) по основным флоу
