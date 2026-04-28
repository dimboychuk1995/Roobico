from flask import Blueprint

customer_portal_bp = Blueprint("customer_portal", __name__)

from app.blueprints.customer_portal import routes  # noqa: E402,F401
