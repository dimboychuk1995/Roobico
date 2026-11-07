# Ночная уборка "NEW Customer" (cleanup_system_customer) — установка на прод

Механик сканирует VIN в мобильном приложении — неизвестный VIN заводит
юнит под системным клиентом магазина **"NEW Customer"** (`customers` с
`is_system: true`). Дальше офис переносит WO на настоящую компанию
(Transfer — исходный юнит под "NEW Customer" деактивируется, у компании
появляется копия) либо юнит так и остаётся: отсканирован, WO не создан.
За месяцы у "NEW Customer" копятся десятки таких юнитов, и владелец
чистит их руками.

Ночной cron (`app/scripts/cleanup_system_customer.py`, логика —
`app/blueprints/customers/services/system_customer.py`) делает это сам.

## Что делает

Для каждого магазина каждого тенанта (все `master.shops` с `db_name`,
включая деактивированные локации — у них тот же мусор):

1. Находит системного клиента: `customers` с `shop_id` магазина и
   `is_system: true`. Обычный клиент, которого просто назвали
   "New Customer", под фильтр не попадает.
2. Берёт все его юниты — активные и деактивированные.
3. **Удаляет** юнит, только если выполнены оба условия:
   - на него нет ни одной ссылки из `work_orders` (включая сметы и
     удалённые WO), `calendar_events`, `annual_inspections` и
     `attachments` (файлы юнита) — список `REFERENCING_COLLECTIONS` в
     сервисе, с комментарием по каждой коллекции;
   - он старше grace-периода (`--grace-hours`, по умолчанию 6 ч) и по
     `created_at`, и по `updated_at` — механик мог только что отсканировать
     VIN и ещё не создать WO. Юнит без `created_at` считается старым.
4. Перед самим удалением, уже внутри транзакции, проверяет ссылки ещё раз:
   юнит, на который между проверкой и удалением появился WO / запись
   календаря / инспекция / вложение, остаётся (счётчик
   `kept_late_referenced`).
5. Вместе с юнитом удаляет его строки в `recall_notifications` (журнал
   отправленных NHTSA-рекаллов — производные данные, без юнита смысла не
   имеют; причиной оставить юнит не считаются).
6. Если в магазине что-то удалено — пишет одно событие в Activity Journal:
   action `delete`, entity `customer` "NEW Customer", user «Nightly
   cleanup», source `System`, в details — полный список удалённых юнитов
   (`deleted_unit_ids` — все id через запятую; `deleted_units` — по
   каждому label, VIN, unit #, id; оба поля — строки, журнал их не режет)
   и счётчики оставленных/пропущенных.

Удаление идёт строго по списку `_id` (плюс `shop_id` как второй
предохранитель), никаких широких фильтров. Скрипт идемпотентен — второй
запуск за ночь ничего не удаляет.

## Что никогда не трогает

- сам документ системного клиента "NEW Customer" (не удаляется, не
  деактивируется, поля не меняются);
- любых других клиентов и их юниты — в том числе обычного клиента с
  именем "New Customer";
- юниты "NEW Customer", на которые есть WO/смета, запись календаря,
  инспекция или вложение — они остаются, пока офис не сделает Transfer.
  Transfer перевешивает на юнит настоящей компании сам WO и привязанные к
  нему записи календаря (исходник под "NEW Customer" деактивируется, если
  создана копия, или просто остаётся без ссылок, если у компании уже был
  такой VIN) — такой исходник уходит следующей ночью. Вложения и annual
  inspections Transfer **не переносит**: они остаются на исходном юните и
  держат его, пока их не перенесут или не удалят руками;
- `work_orders`, `customers`, `attachments`, платежи и любые другие
  коллекции — только читает.

## Cron (root, как billing_renewals / email_orders_inbox)

Ежедневно в 08:30 UTC (02:30/03:30 по US Central — после бэкапа прода в
03:00 UTC и синка dev-базы в 04:00 UTC):

```cron
30 8 * * * cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.cleanup_system_customer >> /var/log/roobico_system_customer_cleanup.log 2>&1 || echo "cleanup_system_customer FAILED $(date -u)" >> /var/log/roobico_system_customer_cleanup.log
```

Пути `/home/deploy/Roobico` и `./venv/bin/python` — те же, что в
`deploy/email_orders_inbox.md`; сверить с фактическими на сервере (те же,
что использует юнит gunicorn'а).

Скрипт печатает строку счётчиков по каждому магазину
(`system_customers= candidates= deleted= kept_referenced=
kept_late_referenced= skipped_recent= recall_notifications_deleted=`),
под ней — по одной строке на каждый удалённый юнит
(`  deleted <id> VIN <vin> unit # <number> <label>`; в `--dry-run` —
`  would delete ...`), итоговую строку `Totals:` и возвращает exit code 1,
если по каким-то магазинам были ошибки (трейсбек — в том же логе,
`cleanup_system_customer: shop ... failed`). Так в
`/var/log/roobico_system_customer_cleanup.log` (stdout cron-строки) лежит
полный список удалённого — через логгер приложения он не идёт (вне debug
INFO-сообщения туда не попадают).

## Проверка после установки / ручной запуск

```bash
# dry-run по всем магазинам: только посчитать, ничего не удаляя
cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.cleanup_system_customer --dry-run

# dry-run по одной shop-базе (db_name из master.shops)
cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.cleanup_system_customer --dry-run --shop-db <db_name>

# боевой прогон по одной shop-базе, юниты моложе 12 часов не трогать (по умолчанию — 6)
cd /home/deploy/Roobico && sudo -u deploy ./venv/bin/python -m app.scripts.cleanup_system_customer --shop-db <db_name> --grace-hours 12
```

Перед первым боевым прогоном на проде — `--dry-run` и сверить
`deleted`/`kept_referenced` с ожиданиями (на реальных данных: у одного
магазина 100 юнитов под "NEW Customer", 18 с WO остаются, 82 без ссылок
уходят). Полный список юнитов, которые были бы удалены, скрипт печатает
сам — по строке `  would delete <id> VIN ... unit # ... <label>` на юнит,
в cron-лог (или в терминал при ручном запуске); в боевом прогоне те же
строки начинаются с `  deleted`, и тот же список целиком лежит в details
события Activity Journal.

## Что смотреть потом

- **Reports → Activity Journal**, фильтр source «System» / поиск «Nightly
  cleanup»: по одному событию на магазин за ночь, в details — полный
  список удалённых юнитов (`deleted_unit_ids`, `deleted_units`: label,
  VIN, unit #, id — строками, без обрезания) и счётчики `kept_referenced`
  / `kept_late_referenced` / `skipped_recent`. Событие пишется только
  если что-то удалено.
- `/var/log/roobico_system_customer_cleanup.log` — вывод скрипта: по
  магазинам счётчики и по строке на каждый удалённый юнит (в dry-run —
  `would delete`), итоги, ошибки; строка `cleanup_system_customer FAILED
  <дата>` означает ненулевой exit code.
- Если юнит «пропал» зря — восстановить нечем (удаление физическое),
  поэтому новую коллекцию со ссылкой на юнит обязательно добавлять в
  `REFERENCING_COLLECTIONS`. Поднять такой юнит можно из бэкапа
  `/var/backups/mongo/` (см. CLAUDE.md, бэкапы прода), коллекция
  `<shop_db>.units`: бэкап прода снимается в 03:00 UTC, уборка идёт в
  08:30 UTC, так что в свежем архиве юнит ещё есть — искать по VIN или id
  из строки cron-лога / details события.
