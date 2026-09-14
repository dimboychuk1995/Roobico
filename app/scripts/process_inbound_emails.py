"""Обработка входящих писем ящиков локаций → парт-ордеры (AI).

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.process_inbound_emails             # все локации
    python -m app.scripts.process_inbound_emails --shop <id> # одна локация
    python -m app.scripts.process_inbound_emails --limit 20

Что делает: берёт письма со статусом `received` (и `error` до 3 попыток),
классифицирует их через AI, создаёт парт-ордеры с пометкой «not confirmed»
или помечает письмо как `ignored`. Зависшие блокировки `processing` старше
15 минут возвращает в очередь; байты вложений старше 30 дней вычищает.

Webhook /inbound/email обычно запускает обработку сразу (фоновый поток);
этот cron — страховка: раз в 2 минуты, см. deploy/email_orders_inbox.md.

Exit code 1, если по каким-то письмам были ошибки (для алертов cron).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.blueprints.parts.services.email_orders import run_inbound_processing  # noqa: E402
from app.extensions import get_master_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop", default="", help="Обработать только магазин с этим ObjectId")
    parser.add_argument("--limit", type=int, default=50, help="Максимум писем на магазин за прогон")
    args = parser.parse_args()

    shop_id = None
    if args.shop:
        try:
            shop_id = ObjectId(args.shop)
        except Exception:
            print(f"Invalid shop id: {args.shop}", file=sys.stderr)
            return 2

    app = create_app()
    with app.app_context():
        summary = run_inbound_processing(get_master_db(), shop_id=shop_id, limit=max(1, args.limit))
        app.logger.info("inbound_emails: %s", summary)
        print(
            "inbound_emails: shops={shops} processed={processed} orders={orders} linked={linked} "
            "ignored={ignored} suggested={suggested} errors={errors} skipped={skipped} purged={purged}".format(**summary)
        )
        return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
