from flask import render_template

from . import admin_panel_bp


# NOTE: there is no @admin_panel_bp.get("/") here — `/` is owned by
# `main.index`, which dispatches to the admin placeholder when the
# request comes in on the admin host. Real admin endpoints will live
# under explicit paths like /admin/login, /admin/users, etc., so they
# don't collide with tenant routes.

