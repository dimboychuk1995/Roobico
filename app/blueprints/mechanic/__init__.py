from flask import Blueprint

mechanic_bp = Blueprint("mechanic", __name__)

from app.blueprints.mechanic import routes  # noqa: E402,F401
