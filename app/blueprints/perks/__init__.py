from flask import Blueprint

perks_bp = Blueprint("perks", __name__, url_prefix="/perks")

from .routes import *  # noqa
