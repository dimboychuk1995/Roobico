"""
Веб-страницы механик-режима: упрощённый WO-флоу без цен.

Тонкие рендеры-оболочки: данные страницы тянут JS-ом из общих
безденежных эндпоинтов /work_orders/api/mechanic/* (см.
app/blueprints/work_orders/routes.py). Доступны и менеджерам —
цен здесь нет ни при каких правах.
"""
from __future__ import annotations

from flask import render_template

from app.blueprints.mechanic import mechanic_bp
from app.utils.auth import login_required
from app.utils.permissions import permission_required


@mechanic_bp.get("/mechanic/work_orders")
@login_required
@permission_required("work_orders.view")
def work_orders_list():
    return render_template("public/mechanic/work_orders.html")


@mechanic_bp.get("/mechanic/work_orders/new")
@login_required
@permission_required("work_orders.create")
def work_order_new():
    return render_template("public/mechanic/work_order_details.html", work_order_id="")


@mechanic_bp.get("/mechanic/work_orders/<work_order_id>")
@login_required
@permission_required("work_orders.view")
def work_order_details(work_order_id):
    return render_template("public/mechanic/work_order_details.html", work_order_id=work_order_id)
