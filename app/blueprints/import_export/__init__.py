from flask import Blueprint

import_export_bp = Blueprint("import_export", __name__, url_prefix="/import-export")

from .routes import *  # noqa
