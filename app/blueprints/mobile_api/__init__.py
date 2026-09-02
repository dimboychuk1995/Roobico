from flask import Blueprint

mobile_api_bp = Blueprint("mobile_api", __name__)

from app.blueprints.mobile_api import routes  # noqa: E402,F401
from app.blueprints.mobile_api import vin_scan  # noqa: E402,F401
