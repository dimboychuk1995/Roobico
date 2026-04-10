from flask import Blueprint

attachments_bp = Blueprint("attachments", __name__, url_prefix="/attachments")

from . import routes  # noqa: E402,F401
