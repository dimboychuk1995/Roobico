"""Education Center: страницы модулей/уроков и API прогресса.

Доступно любому залогиненному пользователю — справка не требует прав
(как AI-помощник). Содержимое уроков только описывает интерфейс; реальные
данные магазина сюда не попадают.
"""
from __future__ import annotations

from flask import abort, jsonify, request, session

from app.blueprints.main.routes import _render_app_page
from app.education import catalog, progress
from app.utils.auth import SESSION_TENANT_ID, SESSION_USER_ID, login_required
from . import education_bp


def _user_id():
    return session.get(SESSION_USER_ID)


def _tenant_id():
    return session.get(SESSION_TENANT_ID)


def _lesson_index(summary: dict) -> list[dict]:
    """Плоский список уроков для живого поиска на главной."""
    done = set(summary.get("completed") or [])
    return [
        {
            "module": m.title,
            "title": l.title,
            "summary": l.summary,
            "url": l.url,
            "minutes": l.minutes,
            "done": l.key in done,
        }
        for m in catalog.MODULES
        for l in m.lessons
    ]


def _render(template: str, **ctx):
    summary = ctx.get("progress") or progress.summary(_user_id())
    ctx["progress"] = summary
    ctx.setdefault("modules", catalog.MODULES)
    ctx.setdefault("edu_lesson", catalog.get_lesson_by_key)
    ctx.setdefault("lesson_index", _lesson_index(summary))
    return _render_app_page(template, active_page="education", **ctx)


@education_bp.get("/")
@login_required
def index():
    return _render("public/education/index.html")


@education_bp.get("/<module_id>/")
@login_required
def module_page(module_id: str):
    module = catalog.get_module(module_id)
    if module is None:
        abort(404)
    return _render("public/education/module.html", module=module)


@education_bp.get("/<module_id>/<lesson_id>")
@login_required
def lesson_page(module_id: str, lesson_id: str):
    lesson = catalog.get_lesson(module_id, lesson_id)
    if lesson is None or not catalog.lesson_template_exists(lesson):
        abort(404)
    module = catalog.get_module(module_id)
    progress.mark_viewed(_user_id(), _tenant_id(), lesson.key)
    prev_lesson, next_lesson = catalog.neighbours(lesson)
    summary = progress.summary(_user_id())
    return _render(
        "public/education/lesson.html",
        module=module,
        lesson=lesson,
        prev_lesson=prev_lesson,
        next_lesson=next_lesson,
        is_completed=lesson.key in summary["completed"],
        progress=summary,
    )


@education_bp.post("/api/progress")
@login_required
def api_set_progress():
    data = request.get_json(silent=True) or {}
    key = str(data.get("lesson") or "")
    completed = bool(data.get("completed", True))
    if not catalog.get_lesson_by_key(key):
        return jsonify(ok=False, error="unknown_lesson"), 404
    summary = progress.set_completed(_user_id(), _tenant_id(), key, completed)
    return jsonify(ok=True, progress=summary)


@education_bp.get("/api/progress")
@login_required
def api_get_progress():
    return jsonify(ok=True, progress=progress.summary(_user_id()))


@education_bp.post("/api/progress/reset")
@login_required
def api_reset_progress():
    progress.reset(_user_id())
    return jsonify(ok=True, progress=progress.summary(_user_id()))
