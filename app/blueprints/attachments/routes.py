"""
Attachments REST API — upload, list, download, delete.

All endpoints are AJAX-friendly (JSON in / JSON out except download).
Files are stored as Binary in the shop-level `attachments` collection.
"""
from __future__ import annotations

from bson import ObjectId
from flask import request, jsonify, session, make_response

from app.blueprints.attachments import attachments_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.attachments import (
    ENTITY_TYPES,
    validate_upload,
    save_attachment,
    list_attachments,
    get_attachment,
    delete_attachment,
)


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _tenant_id_variants():
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    oid = _oid(raw)
    if oid:
        out.add(oid)
    return list(out)


def _get_shop_db(master):
    shop_id_raw = session.get("shop_id")
    shop_oid = _oid(shop_id_raw)
    if not shop_oid:
        return None, None

    tenant_variants = _tenant_id_variants()
    if not tenant_variants:
        return None, None

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_variants}})
    if not shop:
        return None, None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[str(db_name)], shop


# ── Upload (one or many) ─────────────────────────────────────────────
@attachments_bp.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    """
    POST multipart/form-data
      entity_type  — required, one of ENTITY_TYPES
      entity_id    — required, ObjectId string
      parent_id    — optional (e.g. work_order_id for labor attachments)
      files         — one or more files (field name "files")
    """
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return jsonify(ok=False, error="No active shop selected."), 400

    entity_type = request.form.get("entity_type", "").strip()
    entity_id = request.form.get("entity_id", "").strip()
    parent_id = request.form.get("parent_id", "").strip() or None

    if entity_type not in ENTITY_TYPES:
        return jsonify(ok=False, error=f"Invalid entity_type: {entity_type}"), 400
    if not _oid(entity_id):
        return jsonify(ok=False, error="Invalid entity_id."), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify(ok=False, error="No files attached."), 400

    user_id = session.get(SESSION_USER_ID)
    shop_id = session.get("shop_id")
    col = db.attachments

    saved = []
    errors = []
    for f in files:
        err = validate_upload(f)
        if err:
            errors.append({"filename": f.filename, "error": err})
            continue
        try:
            doc = save_attachment(
                col,
                entity_type=entity_type,
                entity_id=entity_id,
                file_storage=f,
                uploaded_by=user_id,
                parent_id=parent_id,
                shop_id=shop_id,
            )
            saved.append(doc)
        except Exception as exc:
            errors.append({"filename": f.filename, "error": str(exc)})

    return jsonify(ok=True, saved=saved, errors=errors)


# ── List ──────────────────────────────────────────────────────────────
@attachments_bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    """
    GET ?entity_type=...&entity_id=...&parent_id=...
    Returns JSON list of attachment metadata (no binary).
    """
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return jsonify(ok=False, error="No active shop selected."), 400

    entity_type = request.args.get("entity_type", "").strip()
    entity_id = request.args.get("entity_id", "").strip()
    parent_id = request.args.get("parent_id", "").strip() or None

    if entity_type not in ENTITY_TYPES:
        return jsonify(ok=False, error=f"Invalid entity_type: {entity_type}"), 400
    if not _oid(entity_id):
        return jsonify(ok=False, error="Invalid entity_id."), 400

    items = list_attachments(db.attachments, entity_type, entity_id, parent_id)
    return jsonify(ok=True, items=items)


# ── Download / view ───────────────────────────────────────────────────
@attachments_bp.route("/api/<attachment_id>/download", methods=["GET"])
@login_required
def api_download(attachment_id):
    """Serve the raw file with proper Content-Type for inline viewing."""
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return jsonify(ok=False, error="No active shop selected."), 404

    doc = get_attachment(db.attachments, attachment_id)
    if not doc:
        return jsonify(ok=False, error="Attachment not found."), 404

    response = make_response(bytes(doc["data"]))
    response.headers["Content-Type"] = doc.get("content_type", "application/octet-stream")

    # Inline for images and PDFs so browser displays them; attachment for other types
    content_type = doc.get("content_type") or "application/octet-stream"
    if content_type.startswith("image/") or content_type == "application/pdf":
        disposition = "inline"
    else:
        disposition = "attachment"
    filename = doc.get("filename", "file")
    response.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response.headers["Cache-Control"] = "private, max-age=3600"

    return response


# ── Delete ────────────────────────────────────────────────────────────
@attachments_bp.route("/api/<attachment_id>/delete", methods=["POST", "DELETE"])
@login_required
def api_delete(attachment_id):
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return jsonify(ok=False, error="No active shop selected."), 400

    ok = delete_attachment(db.attachments, attachment_id)
    if not ok:
        return jsonify(ok=False, error="Attachment not found or already deleted."), 404

    return jsonify(ok=True)
