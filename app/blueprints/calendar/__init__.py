from flask import Blueprint

calendar_bp = Blueprint("calendar", __name__)

from . import routes  # noqa
