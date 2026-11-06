"""Maltai - point d'entree FastAPI.

Workspace IA auto-heberge : chat multi-providers (compatible OpenAI),
agents avec outils, sessions persistees, auth par cookie signe.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from core.config import settings
from core import auth as core_auth
from core import database as db
from core import maintenance
from routes import auth as auth_routes
from routes import billing, chat, external, mcp, memory, notes, ollama, providers, sessions, telegram, terminal, tools, uploads
from routes import tool_run

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    task = asyncio.create_task(maintenance.maintenance_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

# Chemins accessibles sans authentification.
OPEN_PREFIXES = ("/static/", "/api/auth/login", "/api/auth/register", "/api/health",
                 "/api/telegram/webhook/", "/api/external/chat")
OPEN_EXACT = {"/", "/login", "/register", "/favicon.ico", "/robots.txt", "/sitemap.xml", "/llms.txt"}


def _route_exists(scope) -> bool:
    """Vrai si une route (ou un mount) de l'application correspond au chemin."""
    for route in app.router.routes:
        try:
            match, _ = route.matches(scope)
        except Exception:
            continue
        if match != Match.NONE:
            return True
    return False


NOT_FOUND_HTML = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Page introuvable — MaltaiAI</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d14;color:#ecf2f7;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center;padding:24px}a{color:#00d4c4;font-weight:700}</style>
</head><body><main><h1>Page introuvable</h1><p>Cette page n'existe pas ou a été déplacée.</p>
<p><a href="/">Retour à l'accueil MaltaiAI</a></p></main></body></html>"""


@app.exception_handler(StarletteHTTPException)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    """Vraie page 404 HTML hors API (les moteurs voient un 404, pas un soft 404)."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return HTMLResponse(NOT_FOUND_HTML, status_code=404)
    return await http_exception_handler(request, exc)


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
        # URL inexistante : laisser le routage renvoyer une vraie 404. Rediriger
        # vers /login (200) creerait des « soft 404 » pour les moteurs. Les routes
        # existantes restent protegees : elles sont toujours redirigees ci-dessous.
        if not _route_exists(request.scope):
            return await call_next(request)
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
app.include_router(billing.router)


@app.get("/api/health")
def health():
    payload = {"app": settings.APP_NAME, "version": settings.APP_VERSION, "status": "ok"}
    if settings.APP_COMMIT:
        payload["commit"] = settings.APP_COMMIT
    return payload


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.middleware("http")
async def static_cache_and_security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and (path.endswith(".js") or path.endswith(".css")):
        # no-cache (sans no-store) : le navigateur garde une copie mais revalide
        # a chaque fois -> reponse 304 legere, toujours a jour apres deploiement.
        response.headers["Cache-Control"] = "no-cache"
    # Headers de securite.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    # CSP en mode Report-Only : n'applique rien, log seulement les violations
    # dans la console navigateur. A passer en "Content-Security-Policy" une fois
    # verifie qu'aucune violation n'apparait en usage normal.
    response.headers.setdefault(
        "Content-Security-Policy-Report-Only",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "frame-ancestors 'self'; base-uri 'self'; form-action 'self'",
    )
    if settings.SECURE_COOKIES:  # true uniquement en prod HTTPS
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.get("/favicon.ico")
def favicon_route():
    return FileResponse(str(STATIC_DIR / "favicon.png"))


@app.get("/robots.txt")
def robots_route():
    return FileResponse(str(STATIC_DIR / "robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_route():
    return FileResponse(str(STATIC_DIR / "sitemap.xml"), media_type="application/xml")


@app.get("/llms.txt")
def llms_route():
    return FileResponse(str(STATIC_DIR / "llms.txt"), media_type="text/plain; charset=utf-8")


@app.get("/login")
def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


@app.get("/register")
def register_page():
    return FileResponse(str(STATIC_DIR / "register.html"))


@app.get("/")
def site_page():
    return FileResponse(str(STATIC_DIR / "site.html"))


@app.get("/app")
def app_page():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/billing")
def billing_page():
    return FileResponse(str(STATIC_DIR / "billing.html"))


@app.get("/billing/success")
def billing_success_page():
    return FileResponse(str(STATIC_DIR / "billing.html"))


# Ajouté en dernier => middleware le plus externe : s'exécute avant l'auth.
@app.middleware("http")
async def canonical_host_redirect(request: Request, call_next):
    """Redirige www.maltai.fr -> maltai.fr en 301 (évite le duplicate content)."""
    host = request.headers.get("host", "")
    if host.startswith("www."):
        target = f"https://{host[4:]}{request.url.path}"
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=301)
    return await call_next(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.APP_BIND, port=settings.APP_PORT, reload=True)
