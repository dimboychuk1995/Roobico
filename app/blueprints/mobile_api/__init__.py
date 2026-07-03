from flask import Blueprint

mobile_api_bp = Blueprint("mobile_api", __name__)

from app.blueprints.mobile_api import routes  # noqa: E402,F401
