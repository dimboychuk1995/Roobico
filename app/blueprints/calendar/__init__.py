from flask import Blueprint

calendar_bp = Blueprint("calendar", __name__)

from . import routes  # noqa: E402,F401  (registers the routes on the blueprint)

__all__ = ["calendar_bp", "routes"]
