from flask import Blueprint

vendors_bp = Blueprint("vendors", __name__, url_prefix="/vendors")

from . import routes  # noqa: E402,F401