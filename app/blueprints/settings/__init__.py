from flask import Blueprint

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

# импортируем routes чтобы они зарегистрировались на blueprint
from .users.routes import *  # noqa
from .locations.routes import *
from .parts_settings.routes import *      # noqa
from .work_orders.routes import *  # noqa
from .wo_presets.routes import *  # noqa