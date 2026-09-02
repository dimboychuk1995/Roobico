# App Store Connect — материалы для первого релиза Roobico (iOS)

Готовые тексты для копипасты в App Store Connect. Обновлять при смене
функциональности приложения.

## Основное

| Поле | Значение |
|---|---|
| Name | Roobico |
| Subtitle (30 chars) | Shop management for mechanics |
| Category | Business (secondary: Productivity) |
| Age rating | 4+ |
| Privacy Policy URL | https://app.roobico.com/privacy |
| Support URL | https://app.roobico.com |
| Copyright | © 2026 Roobico |

## Promotional Text (170 chars)

Run your repair shop from your phone: work orders, parts, customers,
payments and reports — always in sync with Roobico on the web.

## Description

Roobico is shop-management software for auto repair shops and fleet
maintenance teams. The mobile app puts your whole shop in your pocket and
stays in sync with Roobico on the web.

WORK ORDERS
- Create and edit work orders in the field: labor lines, parts with
  automatic pricing, presets, tax — totals are always calculated the same
  way as in the office.
- Snap a photo of a paper work order and let AI turn it into labor lines
  and parts.
- Email invoices and send estimates to customers for approval.
- Record payments and track balances.

PARTS & INVENTORY
- Search your catalog, check stock, see part history.
- Full parts-order cycle: create orders, receive with stock movement, pay.
- Scan a vendor invoice with the camera — AI fills in the vendor and line
  items for you.

CUSTOMERS & UNITS
- Customers, vehicles and equipment with full service history.
- Scan the VIN barcode with the camera — the vehicle and its owner are
  found instantly, and unknown vehicles are created on the spot with
  make/model/year decoded automatically.
- VIN decoder for fast unit creation.
- Attach photos from the camera or gallery to work orders and units.

FOR MECHANICS
- A focused mechanic mode: assigned jobs, time tracking, job photos —
  without pricing details.
- Push notifications for job assignments and approvals.

AND MORE
- Dashboard metrics, calendar with day timeline, 7 standard reports,
  global search across everything.

Roobico requires an account created by your shop. New shops can sign up
at roobico.com.

## Keywords (100 chars)

auto repair,shop,work order,mechanic,fleet,invoice,parts,inventory,garage,maintenance,estimate

## App Privacy (анкета)

Data collected, linked to identity, NOT used for tracking:

- Contact Info → Name, Email Address, Phone Number (аккаунты пользователей
  и контакты клиентов магазина) — App Functionality
- User Content → Photos or Videos (вложения к WO/юнитам, сканы документов),
  Other User Content (бизнес-записи магазина) — App Functionality
- Identifiers → User ID — App Functionality
- Diagnostics → Crash Data (если включим crash reporting; пока можно не
  указывать)

Tracking (ATT): No. Third-party advertising: No.

## App Review Information

- Sign-in required: YES
- Demo account: owner@roobico-test.com / RoobicoTest2026!
  (тестовый тенант на проде, полный набор данных; следить, чтобы триал был
  продлён на время ревью)
- Notes for reviewer:

  Roobico is a B2B tool for auto repair shops. Accounts are provisioned by
  a shop administrator — there is no in-app registration, which is why the
  app offers sign-in only (Guideline 5.1.1(v) account deletion does not
  apply: users are employees of the business that owns the data; the
  business can deactivate users and request data deletion per our Privacy
  Policy at https://app.roobico.com/privacy).

  The demo account above is an owner role with sample data: feel free to
  create/edit work orders, record payments, scan documents. Camera access
  is used for attaching photos, for AI document scanning (paper work
  orders, vendor invoices), and for scanning vehicle VIN barcodes when
  creating a work order or a vehicle record.

## Скриншоты (обязательные)

- iPhone 6.9″ (1320×2868) — обязательно; 6.5″ подхватится из 6.9″.
- iPad Pro 13″ (2064×2752) — обязательно, пока supportsTablet: true.
- Рекомендуемый набор (5–6 шт.): Dashboard, Work Order details,
  WO editor (labor+parts), Parts + скан инвойса, Calendar, Reports.
- Снимать в Expo Go нельзя — ставить TestFlight-билд на устройство или
  симулятор (`npx expo run:ios`), тестовый тенант, светлая тема.

## Ответ на Guideline 2.1 — Information Needed (реджект от 2026-08-14)

Готовый текст для «Reply to App Review» в App Store Connect. Пункт 1
(скринкаст) прикладывается файлом к ответу; пункты 2–7 — текстом ниже.
Этот же текст (без пункта про recording) продублировать в Notes поле App
Review Information для будущих сабмитов.

> Thank you for the review. Please find the requested information below.
> A screen recording captured on a physical device is attached to this
> reply.
>
> **1. Screen recording**
> The attached recording was captured on ДЕВАЙС (iOS ВЕРСИЯ). It starts
> with launching the app and shows: sign-in (there is no in-app
> registration — see note below), the dashboard, browsing and editing a
> work order (labor and parts lines), attaching a photo (including the
> camera permission prompt), scanning a paper document with AI, scanning
> a vehicle VIN barcode with the camera, parts and customers sections,
> and the push-notification permission prompt. The
> app is free, contains no purchases, subscriptions, or paid content, and
> has no public user-generated content (all data is private business
> records of the shop, visible only to that shop's staff).
>
> **2. Devices and OS versions tested on**
> - ДЕВАЙС 1 (iOS ВЕРСИЯ) — physical device, via TestFlight
> - ДЕВАЙС 2 (iPadOS ВЕРСИЯ) — ЕСЛИ БЫЛО
>
> **3. App functions and target audience**
> Roobico is a B2B shop-management tool for auto repair shops and fleet
> maintenance teams. The target audience is shop owners, service writers,
> and mechanics (employees of a repair business). The app is a mobile
> companion to our web application (https://app.roobico.com) and solves
> the problem of managing shop operations away from the front desk: work
> orders with labor and parts, customer and vehicle records with service
> history, parts inventory and vendor orders, recording customer
> payments, reports, and a restricted "mechanic mode" (assigned jobs and
> time tracking without pricing). AI features let staff photograph a
> paper work order or a vendor invoice and have it converted into
> structured line items.
>
> **4. Setup and access instructions**
> Sign-in is required. Accounts are provisioned by a shop administrator;
> there is no in-app registration (new businesses sign up on the web at
> roobico.com). Demo account for review:
> Email: owner@roobico-test.com
> Password: RoobicoTest2026!
> This is an "owner" role account on a demo business with realistic
> sample data. Feel free to create and edit work orders, add parts,
> record payments, attach photos, and try the AI document scan (Work
> Orders → Scan). No sample files are required — any photographed
> document or photo works.
>
> **5. External services used**
> - Our own backend API at https://app.roobico.com (Flask/MongoDB) — all
>   business data and authentication (email + password against our own
>   user database; no third-party auth providers).
> - OpenAI API (server-side only) — AI document scanning (paper work
>   orders, vendor invoices) and the in-app AI assistant. Images/text are
>   sent from our server to OpenAI for extraction; no data is used for
>   advertising or tracking.
> - Expo Push Notification service — delivery of push notifications (job
>   assignments, estimate approvals).
> - NHTSA vPIC (public U.S. government API) — VIN decoding and safety
>   recall lookups for vehicles.
> - No payment processing happens in the app: the app only records
>   payments customers made to the shop by cash/check/external card
>   terminals. Our SaaS subscription is billed to businesses (not to app
>   users) outside the app, on the web, via Stripe.
>
> **6. Regional differences**
> None. The app functions identically in all regions. (VIN decoding and
> recall data come from the U.S. NHTSA database, so they are most useful
> for vehicles sold in the U.S., but the feature is available everywhere.)
>
> **7. Regulated industry / protected third-party material**
> Not applicable. The app is general-purpose business software for
> vehicle repair shops; it does not operate in a regulated industry
> (no medical, financial, gambling, etc. functionality) and contains no
> protected third-party material.

Перед отправкой: заменить плейсхолдеры ДЕВАЙС/ВЕРСИЯ на реальные
устройства, приложить видео, проверить что триал демо-тенанта продлён.

### Чеклист скринкаста (пункт 1)

Записывать на физическом iPhone с последней iOS, чистая установка
TestFlight-билда (чтобы показались permission-промпты), тестовый тенант.
Запись — Control Center → Screen Recording, начать ДО запуска приложения.

1. Запуск приложения (сплэш → логин).
2. Логин демо-аккаунтом (owner@roobico-test.com).
3. Промпт push-уведомлений — разрешить.
4. Dashboard, переход в Work Orders, открыть WO.
5. Редактирование WO: добавить labor-строку и запчасть, показать тоталы.
6. Прикрепить фото — показать промпт камеры, снять фото.
7. AI-скан бумажного документа (камера уже разрешена).
8. VIN-скан: новый WO → «Scan VIN» → навести на VIN-баркод (подойдёт
   фото баркода с экрана) — юнит и клиент подставляются сами.
9. Parts: поиск, карточка запчасти. Customers: карточка клиента + юнит.
10. Календарь/отчёты коротко. Выход не обязателен.

Ограничений по длительности нет, 2–4 минуты достаточно. Видео < 500 MB.

- `cd mobile && npx eas-cli build --platform ios --profile production --auto-submit`
- Версия 1.0.1 (поднята 2026-09-02 — Apple не принимает два сабмита одной
  версии), buildNumber авто-инкремент (remote, EAS).
- После появления билда в TestFlight: в App Store Connect → App Store →
  выбрать билд → заполнить поля выше → Submit for Review.
