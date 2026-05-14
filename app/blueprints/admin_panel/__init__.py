from flask import Blueprint

# url_prefix is empty: admin lives on its own host (admin.roobico.com),
# enforced by app/__init__.py host-split logic.
admin_panel_bp = Blueprint(
    "admin_panel",
    __name__,
    template_folder="templates",
)

from . import routes  # noqa: E402,F401
