"""
Роуты инвентаризации (stocktake). Список сессий живёт вкладкой на странице
Parts (routes.parts_page), здесь — создание, экран подсчёта и действия.
Логика — в services/stocktake.py.
"""
from __future__ import annotations

from flask import flash, jsonify, redirect, request, session, url_for

from app.utils.auth import login_required, SESSION_USER_ID
from app.utils.permissions import permission_required
from app.utils.display_datetime import format_date_mmddyyyy
from app.utils.parts_locations import flatten_location_tree, load_shop_locations

from . import parts_bp
from .routes import _parts_collections, _oid, _parse_int
from .services.stocktake import (
    add_stocktake_item,
    build_recount_flags,
    cancel_stocktake,
    complete_stocktake,
    count_stocktake_item,
    create_stocktake,
)


def _load_stocktake(shop_db, shop, stocktake_id_raw):
    st_id = _oid(stocktake_id_raw)
    if not st_id:
        return None
    return shop_db.stocktakes.find_one({"_id": st_id, "shop_id": shop["_id"], "is_active": {"$ne": False}})


@parts_bp.post("/stocktakes/create")
@login_required
@permission_required("parts.edit")
def stocktakes_create():
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page", tab="stocktakes"))

    name = (request.form.get("name") or "").strip()
    location_id = _oid((request.form.get("location_id") or "").strip()) or None
    category_id = _oid((request.form.get("category_id") or "").strip()) or None
    user_oid = _oid(session.get(SESSION_USER_ID))

    stocktake, err = create_stocktake(
        parts_coll.database, shop, user_oid,
        name=name, location_id=location_id, category_id=category_id,
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page", tab="stocktakes"))

    flash(f"Stocktake ST-{stocktake['number']} started.", "success")
    return redirect(url_for("parts.stocktake_detail", stocktake_id=str(stocktake["_id"])))


@parts_bp.get("/stocktakes/<stocktake_id>")
@login_required
@permission_required("parts.view")
def stocktake_detail(stocktake_id: str):
    from app.blueprints.main.routes import _render_app_page

    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page", tab="stocktakes"))

    shop_db = parts_coll.database
    stocktake = _load_stocktake(shop_db, shop, stocktake_id)
    if not stocktake:
        flash("Stocktake not found.", "error")
        return redirect(url_for("parts.parts_page", tab="stocktakes"))

    items = list(
        shop_db.stocktake_items.find({"stocktake_id": stocktake["_id"]})
        .sort([("location_path", 1), ("part_number", 1)])
    )
    recount_flags = build_recount_flags(shop_db, shop, stocktake, items) if stocktake.get("status") == "open" else set()

    counted = sum(1 for it in items if it.get("status") == "counted")
    discrepancies = sum(
        1 for it in items
        if it.get("status") == "counted" and int(it.get("variance") or 0) != 0
    )
    variance_value = sum(
        int(it.get("variance") or 0) * float(it.get("average_cost") or 0.0)
        for it in items
        if it.get("status") == "counted"
    )

    for it in items:
        it["id"] = str(it["_id"])
        it["needs_recount"] = it["id"] in recount_flags
        it["variance_value"] = (
            int(it.get("variance") or 0) * float(it.get("average_cost") or 0.0)
            if it.get("status") == "counted" else None
        )

    stocktake_locations = [
        {"id": item["id"], "path": item["path"], "depth": item["depth"]}
        for item in flatten_location_tree(load_shop_locations(shop_db, shop["_id"]))
    ]

    return _render_app_page(
        "public/parts_stocktake_detail.html",
        active_page="parts",
        stocktake=stocktake,
        stocktake_created_label=format_date_mmddyyyy(stocktake.get("created_at")),
        items=items,
        items_counted=counted,
        items_discrepancies=discrepancies,
        variance_value=variance_value,
        recount_count=len(recount_flags),
        stocktake_locations=stocktake_locations,
    )


@parts_bp.post("/stocktakes/<stocktake_id>/count")
@login_required
@permission_required("parts.edit")
def stocktake_count(stocktake_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    shop_db = parts_coll.database
    stocktake = _load_stocktake(shop_db, shop, stocktake_id)
    if not stocktake:
        return jsonify({"ok": False, "error": "Stocktake not found"}), 404

    data = request.get_json(silent=True) or {}
    item_id = _oid(str(data.get("item_id") or ""))
    if not item_id:
        return jsonify({"ok": False, "error": "Invalid item id"}), 400

    counted_qty = _parse_int(data.get("counted_qty"), default=None)
    if counted_qty is None:
        return jsonify({"ok": False, "error": "Counted quantity is required"}), 400

    user_oid = _oid(session.get(SESSION_USER_ID))
    item, err = count_stocktake_item(shop_db, shop, stocktake, item_id, counted_qty, user_oid)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    variance = int(item.get("variance") or 0)
    return jsonify({
        "ok": True,
        "item": {
            "id": str(item["_id"]),
            "counted_qty": int(item.get("counted_qty") or 0),
            "expected_at_count": int(item.get("expected_at_count") or 0),
            "variance": variance,
            "variance_value": round(variance * float(item.get("average_cost") or 0.0), 2),
        },
    })


@parts_bp.post("/stocktakes/<stocktake_id>/items/add")
@login_required
@permission_required("parts.edit")
def stocktake_add_item(stocktake_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    shop_db = parts_coll.database
    stocktake = _load_stocktake(shop_db, shop, stocktake_id)
    if not stocktake:
        return jsonify({"ok": False, "error": "Stocktake not found"}), 404

    data = request.get_json(silent=True) or {}
    part_id = _oid(str(data.get("part_id") or ""))
    if not part_id:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    location_raw = str(data.get("location_id") or "").strip()
    location_id = None
    if location_raw:
        location_id = _oid(location_raw)
        if not location_id:
            return jsonify({"ok": False, "error": "Invalid location id"}), 400
        if locs_coll is None or not locs_coll.find_one({"_id": location_id, "shop_id": shop["_id"]}):
            return jsonify({"ok": False, "error": "Location not found"}), 400

    user_oid = _oid(session.get(SESSION_USER_ID))
    item, err = add_stocktake_item(shop_db, shop, stocktake, part_id, location_id, user_oid)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    return jsonify({"ok": True, "item_id": str(item["_id"])})


@parts_bp.post("/stocktakes/<stocktake_id>/complete")
@login_required
@permission_required("parts.edit")
def stocktake_complete(stocktake_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    shop_db = parts_coll.database
    stocktake = _load_stocktake(shop_db, shop, stocktake_id)
    if not stocktake:
        return jsonify({"ok": False, "error": "Stocktake not found"}), 404

    data = request.get_json(silent=True) or {}
    zero_uncounted = bool(data.get("zero_uncounted"))

    user_oid = _oid(session.get(SESSION_USER_ID))
    summary, err = complete_stocktake(
        shop_db, shop, stocktake, user_oid, zero_uncounted=zero_uncounted,
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    return jsonify({"ok": True, "totals": summary["totals"], "errors": summary["errors"]})


@parts_bp.post("/stocktakes/<stocktake_id>/cancel")
@login_required
@permission_required("parts.edit")
def stocktake_cancel(stocktake_id: str):
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    shop_db = parts_coll.database
    stocktake = _load_stocktake(shop_db, shop, stocktake_id)
    if not stocktake:
        return jsonify({"ok": False, "error": "Stocktake not found"}), 404

    user_oid = _oid(session.get(SESSION_USER_ID))
    err = cancel_stocktake(shop_db, stocktake, user_oid)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})
