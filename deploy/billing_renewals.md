# Автопродление подписок (billing_renewals) — установка на прод

Ночной cron создаёт Stripe-инвойсы тенантам, у которых подписка кончается
в ближайшие 3 дня: с сохранённой картой — автосписание, без карты — письмо
с hosted-страницей оплаты. Продление `subscription_until` делает webhook
`invoice.paid`. Скрипт идемпотентен — пока по тенанту висит открытый
инвойс, второй не создаётся.

## Предварительные условия

1. В `.env` на сервере заданы:
   - `STRIPE_SECRET_KEY` (live: `sk_live_...`),
   - `STRIPE_WEBHOOK_SECRET` (см. ниже),
   - `RESEND_API_KEY` / `RESEND_FROM_EMAIL` — для dunning-писем.
2. В Stripe Dashboard → Developers → Webhooks зарегистрирован endpoint
   `https://app.roobico.com/billing/stripe/webhook` (именно app-хост:
   на public-хосте enforce_host_split отвечает 302, Stripe считает это
   ошибкой доставки) с событиями:
   `invoice.paid`, `invoice.payment_succeeded`, `invoice.payment_failed`,
   `invoice.voided`, `invoice.marked_uncollectible`, `charge.refunded`,
   `customer.updated`, `payment_method.attached`.
   Signing secret этого endpoint'а → `STRIPE_WEBHOOK_SECRET`.
3. В Settings → Billing → Invoice settings (live) включить «Save customer
   payment information» и отключить «Ask customers before saving» — иначе
   карты не прикрепляются и автосписание не работает (Link перехватывает
   оплату, чекбокс по умолчанию снят). Согласие на карту-на-файле
   фиксировать в условиях сервиса.
4. Legacy-тенанты без subscription-полей закрыты бэкфиллом (разово):
   ```
   cd /srv/roobico && venv/bin/python -m app.scripts.backfill_subscriptions --dry-run
   cd /srv/roobico && venv/bin/python -m app.scripts.backfill_subscriptions
   ```

## Cron (root, аналогично mongo_backup)

Ежедневно в 09:00 UTC (03:00–04:00 по US Central — до начала рабочего дня):

```cron
0 9 * * * cd /srv/roobico && venv/bin/python -m app.scripts.billing_renewals >> /var/log/roobico_billing.log 2>&1 || echo "billing_renewals FAILED $(date -u)" >> /var/log/roobico_billing.log
```

Пути `/srv/roobico` и `venv/bin/python` сверить с фактическими на сервере
(те же, что использует юнит gunicorn'а).

Скрипт возвращает exit code 1, если по каким-то тенантам были Stripe-ошибки —
подробности в логе (`grep renewals /var/log/roobico_billing.log`).

## Проверка после установки

```bash
# dry-run: только посчитать, кто due, ничего не создавая в Stripe
cd /srv/roobico && venv/bin/python -m app.scripts.billing_renewals --dry-run

# боевой прогон по одному тенанту
cd /srv/roobico && venv/bin/python -m app.scripts.billing_renewals --tenant <slug>
```

## Что смотреть при разборе инцидентов

- `master_db.billing_invoices` — реестр наших инвойсов (статусы
  open/paid/void/uncollectible/refunded, `extended` — было ли продление).
- `master_db.stripe_events` — обработанные webhook-события (TTL 90 дней).
- `master_db.admin_audit` — записи `tenant.billing.*` (auto_renewal,
  invoice_paid, invoice_failed, refunded) видны рядом с ручными действиями.
- Открытый инвойс старше 14 дней — скрипт пишет warning в лог: тенант не
  платит, Stripe исчерпал ретраи; разруливать из админки (продлить руками /
  void инвойса в Stripe / отключить тенанта).
