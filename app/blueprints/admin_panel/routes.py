from flask import render_template

from . import admin_panel_bp


@admin_panel_bp.get("/")
def index():
    """
    Placeholder landing page for admin.roobico.com.
    Real login flow + admin features will be added in the next steps.
    """
    return render_template("admin_panel/placeholder.html")
