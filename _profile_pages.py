"""
Profile all page-rendering routes to measure load times.
Run: python _profile_pages.py

Requires the app to be importable and a valid MongoDB connection.
This script creates a test client, logs in, and measures each page.
"""
import time
import sys
import os

os.environ.setdefault("FLASK_ENV", "development")

from app import create_app

app = create_app()


def get_test_session(client):
    """Try to login and get a valid session."""
    from app.extensions import get_master_db

    with app.app_context():
        master = get_master_db()
        # Find any active user
        user = master.users.find_one({"is_active": True})
        if not user:
            print("ERROR: No active users found in database")
            sys.exit(1)

        tenant = master.tenants.find_one({"_id": user.get("tenant_id"), "status": "active"})
        if not tenant:
            print("ERROR: No active tenant found for user")
            sys.exit(1)

        shops = list(master.shops.find({"tenant_id": tenant["_id"]}).limit(5))
        if not shops:
            print("ERROR: No shops found for tenant")
            sys.exit(1)

        shop = shops[0]
        shop_ids = [str(s["_id"]) for s in shops]

        print(f"Using user: {user.get('email')}")
        print(f"Tenant: {tenant.get('name')}")
        print(f"Shop: {shop.get('name')} (db: {shop.get('db_name')})")
        print()

        # Set session directly
        with client.session_transaction() as sess:
            sess["user_id"] = str(user["_id"])
            sess["tenant_id"] = str(tenant["_id"])
            sess["shop_id"] = str(shop["_id"])
            sess["shop_ids"] = shop_ids
            sess["user_permissions"] = [
                "dashboard.view",
                "customers.view", "customers.edit", "customers.deactivate",
                "vendors.view", "vendors.edit", "vendors.deactivate",
                "parts.view", "parts.edit",
                "work_orders.view", "work_orders.create", "work_orders.edit",
                "settings.view", "settings.manage_org", "settings.manage_users", "settings.manage_roles",
                "reports.view",
                "import_export.view",
            ]

        return shop


# Pages to test - (name, url, method)
PAGES = [
    ("Dashboard", "/dashboard"),
    ("Calendar", "/calendar"),
    ("Customers List", "/customers"),
    ("Vendors List", "/vendors/"),
    ("Parts List", "/parts/"),
    ("Work Orders List", "/work_orders/"),
    ("Reports Index", "/reports"),
    ("Report: Sales Summary", "/reports/standard/sales_summary"),
    ("Report: Payments Summary", "/reports/standard/payments_summary"),
    ("Report: Customer Balances", "/reports/standard/customer_balances"),
    ("Report: Vendor Balances", "/reports/standard/vendor_balances"),
    ("Report: Audit", "/reports/audit"),
    ("Settings", "/settings"),
    ("Import/Export", "/import-export/"),
    ("Work Order Details (new)", "/work_orders/details"),
]


def measure_page(client, name, url, runs=3):
    """Measure average load time for a page."""
    times = []
    status = None
    size = 0

    for _ in range(runs):
        start = time.perf_counter()
        resp = client.get(url, follow_redirects=True)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        status = resp.status_code
        size = len(resp.data)

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    return {
        "name": name,
        "url": url,
        "avg_ms": round(avg_time * 1000, 1),
        "min_ms": round(min_time * 1000, 1),
        "max_ms": round(max_time * 1000, 1),
        "status": status,
        "size_kb": round(size / 1024, 1),
    }


def main():
    client = app.test_client()
    shop = get_test_session(client)

    # Also test customer details if we can find a customer
    with app.app_context():
        from app.extensions import get_mongo_client
        db_name = shop.get("db_name")
        if db_name:
            mongo = get_mongo_client()
            shop_db = mongo[db_name]
            customer = shop_db.customers.find_one({"is_active": True})
            if customer:
                cid = str(customer["_id"])
                PAGES.append(("Customer Details", f"/customers/{cid}"))
                PAGES.append(("Customer Details (units)", f"/customers/{cid}?tab=units"))
                PAGES.append(("Customer Details (payments)", f"/customers/{cid}?tab=payments"))

            wo = shop_db.work_orders.find_one({"shop_id": shop["_id"], "is_active": True})
            if wo:
                woid = str(wo["_id"])
                PAGES.append(("Work Order Details (existing)", f"/work_orders/details?work_order_id={woid}"))

    print("=" * 90)
    print(f"{'Page':<40} {'Avg ms':>8} {'Min ms':>8} {'Max ms':>8} {'Status':>7} {'Size KB':>8}")
    print("=" * 90)

    results = []
    for name, url in PAGES:
        result = measure_page(client, name, url)
        results.append(result)
        flag = " ⚠️ SLOW" if result["avg_ms"] > 300 else ""
        print(f"{result['name']:<40} {result['avg_ms']:>8.1f} {result['min_ms']:>8.1f} {result['max_ms']:>8.1f} {result['status']:>7} {result['size_kb']:>8.1f}{flag}")

    print("=" * 90)

    # Summary
    slow = [r for r in results if r["avg_ms"] > 300]
    if slow:
        print(f"\n⚠️  {len(slow)} SLOW PAGES (>300ms):")
        for r in sorted(slow, key=lambda x: x["avg_ms"], reverse=True):
            print(f"  {r['name']:<40} {r['avg_ms']:>8.1f}ms")
    else:
        print("\n✅ All pages load under 300ms")


if __name__ == "__main__":
    main()
