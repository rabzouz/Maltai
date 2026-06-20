"""Maltai - point d'entree FastAPI.

Workspace IA auto-heberge : chat multi-providers (compatible OpenAI),
agents avec outils, sessions persistees, auth par cookie signe.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core import auth as core_auth
from core import database as db
from routes import auth as auth_routes
from routes import chat, external, mcp, memory, notes, ollama, providers, sessions, telegram, terminal, tools, uploads
from routes import tool_run

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

# Chemins accessibles sans authentification.
OPEN_PREFIXES = ("/static/", "/api/auth/login", "/api/health",
                 "/api/telegram/webhook/", "/api/external/chat")
OPEN_EXACT = {"/", "/login", "/favicon.ico"}


@app.on_event("startup")
def _startup():
    db.init_db()
    core_auth.seed_admin()
    if not db.list_providers():
        db.add_provider(
            settings.DEFAULT_PROVIDER_NAME,
            settings.DEFAULT_BASE_URL,
            settings.DEFAULT_API_KEY,
            settings.DEFAULT_MODEL,
            settings.DEFAULT_EMBED_MODEL,
        )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    request.state.user = None
    path = request.url.path

    # Resolution de l'utilisateur depuis le cookie (meme si bypass, pour /me).
    token = request.cookies.get(core_auth.COOKIE_NAME)
    if token:
        uid = core_auth.verify_token(token)
        if uid:
            request.state.user = core_auth.get_user(uid)

    if not settings.AUTH_ENABLED:
        return await call_next(request)

    client_host = request.client.host if request.client else ""
    if settings.LOCALHOST_BYPASS and client_host in ("127.0.0.1", "::1"):
        return await call_next(request)

    if path in OPEN_EXACT or any(path.startswith(p) for p in OPEN_PREFIXES):
        return await call_next(request)

    if request.state.user is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Non authentifie"}, status_code=401)
        return RedirectResponse("/login")

    return await call_next(request)


app.include_router(auth_routes.router)
app.include_router(providers.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(ollama.router)
app.include_router(mcp.router)
app.include_router(telegram.router)
app.include_router(terminal.router)
app.include_router(external.router)
app.include_router(uploads.router)
app.include_router(tools.router)
app.include_router(notes.router)
app.include_router(tool_run.router)
app.include_router(uploads.download_router)


@app.get("/api/health")
def health():
    return {"app": settings.APP_NAME, "version": settings.APP_VERSION, "status": "ok"}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and (path.endswith(".js") or path.endswith(".css")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/favicon.ico")
def favicon_route():
    return FileResponse(str(STATIC_DIR / "favicon.png"))


@app.get("/login")
def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


@app.get("/")
def site_page():
    return FileResponse(str(STATIC_DIR / "site.html"))


@app.get("/app")
def app_page():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.APP_BIND, port=settings.APP_PORT, reload=True)
