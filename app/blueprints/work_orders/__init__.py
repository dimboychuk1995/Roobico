from flask import Blueprint

work_orders_bp = Blueprint("work_orders", __name__)

from . import routes  # noqa: E402,F401
from . import vin_api  # noqa: E402,F401
from . import recalls_api  # noqa: E402,F401
