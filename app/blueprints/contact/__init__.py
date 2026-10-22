from flask import Blueprint

# Публичная форма «Contact us» со страницы логина. Заявки читаются в
# админке (admin_panel → Contact requests).
contact_bp = Blueprint("contact", __name__)

from . import routes  # noqa: E402,F401
