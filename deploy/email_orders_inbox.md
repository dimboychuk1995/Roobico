# Ящик локации для парт-ордеров (email → AI → parts orders) — установка на прод

Каждая локация получает адрес `orders-<token>@roobico.com`. Владелец
пересылает туда почту (или даёт адрес вендорам как CC). Cloudflare Email
Routing отдаёт письмо Worker'у, Worker POST'ит сырой MIME в
`https://app.roobico.com/inbound/email`, приложение сохраняет письмо в
`<shop_db>.inbound_emails` и сразу обрабатывает его в фоновом потоке
(классификация AI → извлечение → парт-ордер с `needs_confirmation=True`).
Cron-скрипт — страховка для писем, которые фоновая обработка не добила.

## 1. `.env` на сервере

```
INBOUND_EMAIL_WEBHOOK_SECRET=<длинная случайная строка>   # python -c "import secrets; print(secrets.token_urlsafe(32))"
INBOUND_EMAIL_DOMAIN=roobico.com                          # домен адресов локаций (по умолчанию roobico.com)
INBOUND_EMAIL_LOCAL_PREFIX=orders-                        # префикс local-part (по умолчанию orders-)
INBOUND_EMAIL_PROCESS_INLINE=true                         # обрабатывать письмо сразу в фоновом потоке
OPENAI_API_KEY=...                                        # уже есть (AI Order Reader)
```

После правки `.env` — перезапуск gunicorn (через `deploy/deploy_prod.ps1` или
`systemctl restart`). Индексы (`uniq_shop_inbound_email_token`,
`inbound_emails`, `vendors.email_senders`) создаются при старте приложения.

## 2. Cloudflare Email Worker

1. Cloudflare Dashboard → Workers & Pages → Create → Worker, имя
   `roobico-inbound-email`. Вставить код `deploy/cloudflare_email_worker.js`,
   Deploy.
2. Worker → Settings → Variables and Secrets:
   - `INBOUND_URL` = `https://app.roobico.com/inbound/email`
   - `INBOUND_SECRET` = значение `INBOUND_EMAIL_WEBHOOK_SECRET` (тип Secret).
3. Домен `roobico.com` → Email → Email Routing → Routing rules:
   - существующие правила `billing@` / `workorders@` → gmail **оставить**
     (конкретные адреса имеют приоритет над catch-all);
   - **Catch-all address** → Action «Send to a Worker» → `roobico-inbound-email`
     → Save. Если catch-all уже используется для чего-то другого — вместо него
     нельзя задать wildcard-правило `orders-*`, поэтому catch-all и есть путь;
     Worker сам отбрасывает всё, что не похоже на `orders-<token>@`.
4. Проверка: Settings → Integrations → Email orders inbox на тестовом
   тенанте → включить → скопировать адрес → отправить на него письмо с
   PDF-инвойсом. В течение минуты письмо появляется в «Inbox history»; в
   логе gunicorn — строки `inbound_email`/`email_orders`. Если письмо
   не дошло: Worker → Logs (Real-time logs) покажет ответ webhook'а
   (401 — секреты не совпадают, 503 — секрет не задан в `.env`,
   413 — письмо больше 16 МБ).

Ограничения: сырое письмо должно быть меньше 16 МБ (`MAX_CONTENT_LENGTH`);
из вложений сохраняются только PDF/изображения до 6 МБ каждое и 8 МБ суммарно.

## 3. Cron (root, как billing_renewals)

Каждые 2 минуты — добить письма, которые не обработались inline (перезапуск
gunicorn посреди обработки, временная ошибка OpenAI), вернуть зависшие
блокировки и почистить байты вложений старше 30 дней:

```cron
*/2 * * * * cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.process_inbound_emails >> /var/log/roobico_inbound_emails.log 2>&1
```

Скрипт печатает сводку (`processed/orders/ignored/errors`) и возвращает exit
code 1, если были ошибки. Ручной запуск для одной локации:

```bash
cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.process_inbound_emails --shop <shop ObjectId>
```

## 4. Что смотреть при разборе инцидентов

- `<shop_db>.inbound_emails` — все письма локации: `status`
  (`received` → `processing` → `order_created` / `linked` / `ignored` /
  `suggested` / `error` / `rejected`), `classification` (kind, confidence,
  reason), `extracted` (что прочитал AI), `error`, `attempts`.
- `<shop_db>.parts_orders` с `needs_confirmation: true` — заказы, ждущие
  человека; `source.inbound_email_id` ведёт к письму.
- `master.shops.inbound_email_token` — токен адреса локации (уникальный
  sparse-индекс). Смена адреса из UI («Issue a new address») перезаписывает
  токен, старые письма остаются.
- Письма, не попавшие ни в одну локацию (адрес с неизвестным токеном), в
  базу не пишутся — только warning `inbound_email: unrouted message` в логе.
- Стоимость AI: классификация — gpt-4o-mini (доли цента), извлечение —
  gpt-4o (PDF-страница ≈ 1–3 цента). Ключ тот же, что у AI Order Reader.
