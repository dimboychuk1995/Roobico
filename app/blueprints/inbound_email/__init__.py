from flask import Blueprint

inbound_email_bp = Blueprint("inbound_email", __name__, url_prefix="/inbound")

from . import routes  # noqa: E402,F401
