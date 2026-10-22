"""Education Center — учебные модули и уроки по каждому разделу Roobico.

Каталог (модули → уроки) живёт в catalog.py; тела уроков — Jinja-шаблоны
в templates/public/education/lessons/<module>/<lesson>.html, скриншоты — в
static/img/education/. Каталог и подписи скриншотов использует и
AI-помощник (assistant/services/chat.py): он может вставлять в ответ
скриншоты и ссылки на уроки.
"""
