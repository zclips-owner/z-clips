"""Shared Jinja2 templates instance and render helper.

Routers import ``render`` from here so every page renders the same way.
``base_context`` (app.deps) supplies current_user / is_owner / csrf_token.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def render(request, template: str, context: dict | None = None, status_code: int = 200):
    data = {"request": request, **(context or {})}
    return templates.TemplateResponse(request, template, data, status_code=status_code)
