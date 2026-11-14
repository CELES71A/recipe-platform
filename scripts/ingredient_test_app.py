"""Temporary Stage 2 ASGI entry point until the shared main.py registration is approved.

Run from the repository root with both import directories, for example:
PYTHONPATH=backend:scripts python -m uvicorn ingredient_test_app:app
This uses the original app, middleware, errors, lifespan, and static mount.
"""
from starlette.routing import Mount

from app.main import app
from app.ingredient_routes import router

static_mount = app.router.routes[-1]
if not isinstance(static_mount, Mount) or static_mount.path != '':
    raise RuntimeError('Baseline static mount changed; review the registration position')
app.router.routes.pop()
app.include_router(router)
app.router.routes.append(static_mount)
