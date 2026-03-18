import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import Config

ZIP_REGEX = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync ZIP sales tax rates into master_db.zip_sales_tax_rates."
    )
    parser.add_argument(
        "--source",
        choices=["api_ninjas", "csv"],
        default="api_ninjas",
        help="Data source. api_ninjas requires SALES_TAX_API_KEY env var.",
    )
    parser.add_argument(
        "--csv",
        default="",
        help="Path to CSV file when --source=csv. Required columns: zip_code, combined_rate.",
    )
    parser.add_argument(
        "--zip",
        action="append",
        default=[],
        help="Specific ZIP to sync. Can be repeated. If omitted, ZIPs are extracted from shop addresses.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to DB.",
    )
    return parser.parse_args()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_zip(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = ZIP_REGEX.search(text)
    if not match:
        return ""
    return match.group(1)


def resolve_shop_db_name(shop: dict) -> str:
    return (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
        or ""
    )


def collect_shop_zips(master_db) -> list[str]:
    zips = set()
    projection = {
        "zip": 1,
        "address": 1,
        "address_line": 1,
        "db_name": 1,
        "database": 1,
        "db": 1,
        "mongo_db": 1,
        "shop_db": 1,
    }
    for shop in master_db.shops.find({}, projection):
        if not resolve_shop_db_name(shop):
            continue

        candidates = [shop.get("zip"), shop.get("address"), shop.get("address_line")]
        normalized = ""
        for value in candidates:
            normalized = normalize_zip(value)
            if normalized:
                break
        if normalized:
            zips.add(normalized)
    return sorted(zips)


def fetch_from_api_ninjas(zip_code: str, api_key: str) -> dict:
    # Docs: https://api.api-ninjas.com/v1/salestax?zip_code=<ZIP>
    base_url = "https://api.api-ninjas.com/v1/salestax"
    url = f"{base_url}?{urlencode({'zip_code': zip_code})}"
    req = Request(url, headers={"X-Api-Key": api_key, "Accept": "application/json"}, method="GET")
    with urlopen(req, timeout=20) as resp:
        payload = resp.read().decode("utf-8")
        data = json.loads(payload)

    if isinstance(data, list) and data:
        item = data[0]
    elif isinstance(data, dict):
        item = data
    else:
        raise RuntimeError(f"Unexpected API response for ZIP {zip_code}: {data}")

    combined_rate = item.get("total_rate")
    if combined_rate is None:
        combined_rate = item.get("combined_rate")
    if combined_rate is None:
        raise RuntimeError(f"API response has no combined rate for ZIP {zip_code}")

    def _to_float(value):
        try:
            return float(value)
        except Exception:
            return 0.0

    return {
        "zip_code": zip_code,
        "country": "US",
        "combined_rate": _to_float(combined_rate),
        "state_rate": _to_float(item.get("state_rate")),
        "county_rate": _to_float(item.get("county_rate")),
        "city_rate": _to_float(item.get("city_rate")),
        "special_rate": _to_float(item.get("special_rate")),
        "state": str(item.get("state") or "").strip(),
        "city": str(item.get("city") or "").strip(),
        "source": "api_ninjas",
    }


def load_csv_rates(csv_path: str) -> dict[str, dict]:
    rates = {}
    with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"zip_code", "combined_rate"}
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise RuntimeError(f"CSV is missing required columns: {', '.join(missing)}")

        for row in reader:
            zip_code = normalize_zip(row.get("zip_code"))
            if not zip_code:
                continue

            def _to_float(value):
                try:
                    return float(value)
                except Exception:
                    return 0.0

            rates[zip_code] = {
                "zip_code": zip_code,
                "country": str(row.get("country") or "US").strip() or "US",
                "combined_rate": _to_float(row.get("combined_rate")),
                "state_rate": _to_float(row.get("state_rate")),
                "county_rate": _to_float(row.get("county_rate")),
                "city_rate": _to_float(row.get("city_rate")),
                "special_rate": _to_float(row.get("special_rate")),
                "state": str(row.get("state") or "").strip(),
                "city": str(row.get("city") or "").strip(),
                "source": "csv",
            }
    return rates


def main() -> None:
    load_dotenv()
    args = parse_args()

    client = MongoClient(Config.MONGO_URI)
    master_db = client[Config.MASTER_DB_NAME]

    input_zips = {normalize_zip(z) for z in args.zip if normalize_zip(z)}
    if input_zips:
        zips_to_sync = sorted(input_zips)
    else:
        zips_to_sync = collect_shop_zips(master_db)

    if not zips_to_sync:
        print("No ZIP codes found in shops. Add ZIP to shop address/zip first.")
        return

    now = utcnow()
    upserted = 0
    skipped = 0

    csv_rates = {}
    if args.source == "csv":
        if not args.csv:
            raise RuntimeError("--csv is required when --source=csv")
        csv_rates = load_csv_rates(args.csv)

    api_key = os.environ.get("SALES_TAX_API_KEY", "").strip()
    if args.source == "api_ninjas" and not api_key and args.dry_run:
        print("SALES_TAX_API_KEY is not set. Dry-run will only list discovered ZIPs.")
        for zip_code in zips_to_sync:
            print(f"{zip_code}: discovered (dry-run)")
        print(f"Done. discovered={len(zips_to_sync)}, total={len(zips_to_sync)}")
        return

    if args.source == "api_ninjas" and not api_key:
        raise RuntimeError(
            "SALES_TAX_API_KEY is not set. "
            "Create API key and set env var, or run with --source=csv."
        )

    for zip_code in zips_to_sync:
        try:
            if args.source == "api_ninjas":
                rate = fetch_from_api_ninjas(zip_code, api_key)
            else:
                rate = csv_rates.get(zip_code)
                if not rate:
                    skipped += 1
                    print(f"{zip_code}: skipped (not found in CSV)")
                    continue

            payload = {
                "zip_code": zip_code,
                "country": rate.get("country") or "US",
                "combined_rate": float(rate.get("combined_rate") or 0),
                "state_rate": float(rate.get("state_rate") or 0),
                "county_rate": float(rate.get("county_rate") or 0),
                "city_rate": float(rate.get("city_rate") or 0),
                "special_rate": float(rate.get("special_rate") or 0),
                "state": rate.get("state") or "",
                "city": rate.get("city") or "",
                "source": rate.get("source") or args.source,
                "source_updated_at": now,
                "is_active": True,
                "updated_at": now,
            }

            if args.dry_run:
                print(f"{zip_code}: {payload['combined_rate']:.6f} (dry-run)")
                upserted += 1
                continue

            master_db.zip_sales_tax_rates.update_one(
                {"zip_code": zip_code},
                {
                    "$set": payload,
                    "$setOnInsert": {
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            upserted += 1
            print(f"{zip_code}: upserted combined_rate={payload['combined_rate']:.6f}")

        except HTTPError as exc:
            skipped += 1
            print(f"{zip_code}: HTTP error {exc.code}")
        except URLError as exc:
            skipped += 1
            print(f"{zip_code}: URL error {exc.reason}")
        except Exception as exc:
            skipped += 1
            print(f"{zip_code}: failed ({exc})")

    print(f"Done. upserted={upserted}, skipped={skipped}, total={len(zips_to_sync)}")


if __name__ == "__main__":
    main()
