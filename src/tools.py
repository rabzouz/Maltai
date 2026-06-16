"""Outils de l'agent Maltai.

Chaque outil = nom + description + schema JSON (format OpenAI function
calling) + une fonction async run(args, ctx) -> str.

ctx : {"is_admin": bool} — le shell est reserve aux admins.
Les outils fichiers sont sandboxes dans data/workspace/.
"""
from __future__ import annotations

import ast
import asyncio
import datetime
import html
import ipaddress
import json
import operator
import base64
import re
import socket
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin
from typing import Any, Awaitable, Callable

import httpx

from core.config import DATA_DIR
from core import plans

WORKSPACE = DATA_DIR / "workspace"
WORKSPACE.mkdir(exist_ok=True)

MAX_TOOL_OUTPUT = 6000  # caracteres renvoyes au modele


def _truncate(s: str) -> str:
    if len(s) <= MAX_TOOL_OUTPUT:
        return s
    return s[:MAX_TOOL_OUTPUT] + f"\n…[tronque, {len(s)} caracteres au total]"


def _user_workspace(ctx: dict | None = None) -> Path:
    """Retourne le workspace isole par utilisateur."""
    uid = (ctx or {}).get("user_id") or "shared"
    uid = "".join(c for c in str(uid) if c.isalnum() or c in "-_")[:32] or "shared"
    ws = WORKSPACE / uid
    ws.mkdir(parents=True, exist_ok=True)
    return ws

def _safe_path(rel: str, ctx: dict | None = None) -> Path:
    """Resout un chemin relatif DANS le workspace utilisateur, refuse toute evasion."""
    ws = _user_workspace(ctx)
    p = (ws / rel).resolve()
    if not str(p).startswith(str(ws.resolve())):
        raise ValueError("Chemin hors du workspace refuse")
    return p


# --- Calculatrice (eval AST sans danger) -----------------------------------

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("Expression non autorisee")


async def tool_calculator(args: dict, ctx: dict) -> str:
    expr = str(args.get("expression", ""))
    try:
        result = _eval_node(ast.parse(expr, mode="eval"))
        return str(result)
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as e:
        return f"Erreur de calcul : {e}"


# --- Fichiers (sandbox data/workspace) --------------------------------------

async def tool_list_files(args: dict, ctx: dict) -> str:
    try:
        target = _safe_path(str(args.get("path", ".")), ctx)
    except ValueError as e:
        return str(e)
    if not target.exists():
        return "Dossier inexistant"
    ws = _user_workspace(ctx)
    lines = []
    for p in sorted(target.rglob("*")):
        rel = p.relative_to(ws)
        lines.append(f"{'[D]' if p.is_dir() else '[F]'} {rel}")
        if len(lines) >= 200:
            lines.append("…")
            break
    return "\n".join(lines) or "(workspace vide)"


async def tool_read_file(args: dict, ctx: dict) -> str:
    try:
        p = _safe_path(str(args.get("path", "")), ctx)
    except ValueError as e:
        return str(e)
    if not p.is_file():
        return "Fichier introuvable"
    try:
        return _truncate(p.read_text(errors="replace"))
    except OSError as e:
        return f"Erreur lecture : {e}"


async def tool_write_file(args: dict, ctx: dict) -> str:
    try:
        p = _safe_path(str(args.get("path", "")), ctx)
    except ValueError as e:
        return str(e)
    content = str(args.get("content", ""))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Ecrit : {p.relative_to(_user_workspace(ctx))} ({len(content)} caracteres)"
    except OSError as e:
        return f"Erreur ecriture : {e}"


# --- Web ---------------------------------------------------------------------

_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>")


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s{2,}", " ", text).strip()


_BROWSER_STATE: dict[str, dict[str, Any]] = {}


def _browser_key(ctx: dict | None) -> str:
    return str((ctx or {}).get("user_id") or "shared")


def _resolve_browser_url(url: str, ctx: dict | None) -> str:
    url = (url or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    current = _BROWSER_STATE.get(_browser_key(ctx), {}).get("url", "")
    if current and url.startswith("/"):
        base = re.match(r"^https?://[^/]+", current)
        if base:
            return base.group(0) + url
    return url


def _html_title(raw: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    return _strip_html(match.group(1)) if match else ""


def _html_links(raw: str, base_url: str, limit: int = 20) -> list[dict[str, str]]:
    links = []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, re.I | re.S):
        href = html.unescape(href.strip())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if href.startswith("/"):
            base = re.match(r"^https?://[^/]+", base_url)
            if base:
                href = base.group(0) + href
        elif not href.startswith(("http://", "https://")):
            href = base_url.rstrip("/") + "/" + href.lstrip("./")
        text = _strip_html(label)[:120] or href
        links.append({"text": text, "url": href})
        if len(links) >= limit:
            break
    return links


def _html_forms(raw: str, base_url: str = "", limit: int = 10) -> list[dict[str, Any]]:
    forms = []
    for index, form in enumerate(re.findall(r"<form\b[^>]*>.*?</form>", raw, re.I | re.S)[:limit]):
        method = re.search(r'\bmethod=["\']?([^"\'>\s]+)', form, re.I)
        action = re.search(r'\baction=["\']?([^"\'>\s]+)', form, re.I)
        inputs = []
        for tag in re.findall(r"<(?:input|textarea|select)\b[^>]*>", form, re.I | re.S):
            name = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.I)
            if not name:
                continue
            typ = re.search(r'\btype=["\']?([^"\'>\s]+)', tag, re.I)
            value = re.search(r'\bvalue=["\']([^"\']*)["\']', tag, re.I)
            inputs.append({
                "name": html.unescape(name.group(1)),
                "type": (typ.group(1).lower() if typ else "text"),
                "value": html.unescape(value.group(1)) if value else "",
            })
        action_url = html.unescape(action.group(1)) if action else base_url
        if base_url:
            action_url = urljoin(base_url, action_url or base_url)
        forms.append({
            "index": index,
            "method": (method.group(1).upper() if method else "GET"),
            "action": action_url,
            "fields": inputs[:30],
        })
    return forms


async def _browser_fetch(url: str, ctx: dict | None) -> tuple[str, str, str]:
    target = _resolve_browser_url(url, ctx)
    if not target.startswith(("http://", "https://")):
        raise ValueError("URL invalide (http/https requis)")
    host = re.sub(r"^https?://", "", target).split("/")[0].split(":")[0]
    if not (ctx or {}).get("is_admin") and _is_private_host(host):
        raise ValueError("Refuse : hote prive/interne (reserve aux administrateurs)")
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.get(target, headers={"User-Agent": "Mozilla/5.0 (Maltai Browser)"})
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise ValueError(f"Erreur browser : {e}") from e
    return str(r.url), r.headers.get("content-type", ""), r.text


async def tool_browser_navigate(args: dict, ctx: dict) -> str:
    url = str(args.get("url", "")).strip()
    try:
        final_url, ctype, raw = await _browser_fetch(url, ctx)
    except ValueError as e:
        return str(e)
    title = _html_title(raw) if "html" in ctype else ""
    text = _strip_html(raw) if "html" in ctype else raw
    state = {
        "url": final_url,
        "title": title,
        "content_type": ctype,
        "text": text,
        "links": _html_links(raw, final_url) if "html" in ctype else [],
        "forms": _html_forms(raw, final_url) if "html" in ctype else [],
    }
    _BROWSER_STATE[_browser_key(ctx)] = state
    return _truncate(
        f"URL: {final_url}\nTitle: {title or '-'}\nContent-Type: {ctype or '-'}\n\n"
        f"{text[:2500]}"
    )


async def tool_browser_snapshot(args: dict, ctx: dict) -> str:
    url = str(args.get("url", "")).strip()
    if url:
        nav = await tool_browser_navigate({"url": url}, ctx)
        if nav.startswith("Erreur") or nav.startswith("URL invalide"):
            return nav
    state = _BROWSER_STATE.get(_browser_key(ctx))
    if not state:
        return "Aucune page ouverte. Appelle browser_navigate avec une URL."
    lines = [
        f"URL: {state.get('url')}",
        f"Title: {state.get('title') or '-'}",
        f"Content-Type: {state.get('content_type') or '-'}",
        "",
        "Texte:",
        str(state.get("text") or "")[:2500],
    ]
    links = state.get("links") or []
    if links:
        lines += ["", "Liens:"]
        lines += [f"- {item['text']} -> {item['url']}" for item in links[:15]]
    forms = state.get("forms") or []
    if forms:
        lines += ["", "Formulaires:"]
        lines += [
            f"- #{f['index']} {f['method']} {f['action'] or '(page courante)'} "
            f"fields={', '.join(field['name'] for field in f['fields']) or '-'}"
            for f in forms[:6]
        ]
    return _truncate("\n".join(lines))


async def tool_browser_links(args: dict, ctx: dict) -> str:
    state = _BROWSER_STATE.get(_browser_key(ctx))
    if not state:
        return "Aucune page ouverte. Appelle browser_navigate avec une URL."
    links = state.get("links") or []
    if not links:
        return "Aucun lien trouve."
    return "\n".join(f"- {item['text']}\n  {item['url']}" for item in links[:50])


async def tool_browser_form_list(args: dict, ctx: dict) -> str:
    url = str(args.get("url", "")).strip()
    if url:
        nav = await tool_browser_navigate({"url": url}, ctx)
        if nav.startswith("Erreur") or nav.startswith("URL invalide") or nav.startswith("Refuse"):
            return nav
    state = _BROWSER_STATE.get(_browser_key(ctx))
    if not state:
        return "Aucune page ouverte. Appelle browser_navigate avec une URL."
    forms = state.get("forms") or []
    if not forms:
        return "Aucun formulaire trouve."
    lines = []
    for form in forms:
        fields = ", ".join(
            f"{field['name']}:{field['type']}" + (f"={field['value']}" if field.get("value") else "")
            for field in form.get("fields", [])
        )
        lines.append(
            f"#{form['index']} {form['method']} {form['action'] or state.get('url')}\n"
            f"  fields: {fields or '-'}"
        )
    return _truncate("\n".join(lines))


async def tool_browser_submit(args: dict, ctx: dict) -> str:
    state = _BROWSER_STATE.get(_browser_key(ctx))
    if not state:
        return "Aucune page ouverte. Appelle browser_navigate avec une URL."
    forms = state.get("forms") or []
    if not forms:
        return "Aucun formulaire sur la page actuelle."
    index = int(args.get("index", 0))
    if index < 0 or index >= len(forms):
        return f"Formulaire #{index} introuvable"
    form = forms[index]
    data = args.get("data") or {}
    if not isinstance(data, dict):
        return "data doit etre un objet JSON"
    payload = {field["name"]: field.get("value", "") for field in form.get("fields", [])}
    payload.update({str(k): str(v) for k, v in data.items()})
    method = str(args.get("method") or form.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        return "Seules les methodes GET et POST sont supportees par browser_submit"
    action = str(args.get("action") or form.get("action") or state.get("url"))
    action = urljoin(str(state.get("url")), action)
    host = re.sub(r"^https?://", "", action).split("/")[0].split(":")[0]
    if not ctx.get("is_admin") and _is_private_host(host):
        return "Refuse : hote prive/interne (reserve aux administrateurs)"
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            if method == "GET":
                r = await client.get(action, params=payload, headers={"User-Agent": "Mozilla/5.0 (Maltai Browser)"})
            else:
                r = await client.post(action, data=payload, headers={"User-Agent": "Mozilla/5.0 (Maltai Browser)"})
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Erreur submit : {e}"
    ctype = r.headers.get("content-type", "")
    raw = r.text
    text = _strip_html(raw) if "html" in ctype else raw
    final_url = str(r.url)
    _BROWSER_STATE[_browser_key(ctx)] = {
        "url": final_url,
        "title": _html_title(raw) if "html" in ctype else "",
        "content_type": ctype,
        "text": text,
        "links": _html_links(raw, final_url) if "html" in ctype else [],
        "forms": _html_forms(raw, final_url) if "html" in ctype else [],
    }
    return _truncate(
        f"Submitted #{index} {method} {action}\n"
        f"Final URL: {final_url}\n"
        f"Status: {r.status_code}\n\n{text[:2500]}"
    )


async def tool_web_fetch(args: dict, ctx: dict) -> str:
    url = str(args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return "URL invalide (http/https requis)"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Maltai/0.2"})
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Erreur fetch : {e}"
    ctype = r.headers.get("content-type", "")
    body = r.text
    if "html" in ctype:
        body = _strip_html(body)
    return _truncate(body)


async def tool_web_search(args: dict, ctx: dict) -> str:
    """Recherche DuckDuckGo (HTML lite, sans cle API)."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Requete vide"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Maltai/0.2)"},
            )
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Erreur recherche : {e}"
    results = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        r.text, re.S,
    )
    snippets = re.findall(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S
    )
    out = []
    for i, (href, title) in enumerate(results[:6]):
        snip = _strip_html(snippets[i]) if i < len(snippets) else ""
        out.append(f"- {_strip_html(title)}\n  {href}\n  {snip}")
    return _truncate("\n".join(out)) or "Aucun resultat"


# --- Shell (admin uniquement) ------------------------------------------------

async def tool_shell(args: dict, ctx: dict) -> str:
    if not ctx.get("is_admin"):
        return "Refuse : outil shell reserve aux administrateurs"
    command = str(args.get("command", ""))
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(WORKSPACE),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = out.decode(errors="replace")
        return _truncate(f"[exit {proc.returncode}]\n{text}")
    except asyncio.TimeoutError:
        proc.kill()
        return "Timeout (30s) — commande interrompue"
    except OSError as e:
        return f"Erreur shell : {e}"




# --- Date / heure -------------------------------------------------------------

async def tool_get_datetime(args: dict, ctx: dict) -> str:
    now = datetime.datetime.now().astimezone()
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    return (f"{jours[now.weekday()]} {now.strftime('%d/%m/%Y %H:%M:%S')} "
            f"(fuseau {now.tzname() or now.strftime('%z')})")


# --- Requete HTTP generique (anti-SSRF) ----------------------------------------

def _is_private_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # irresoluble = refuse
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


async def tool_http_request(args: dict, ctx: dict) -> str:
    url = str(args.get("url", ""))
    method = str(args.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        return "Methode non autorisee"
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if not ctx.get("is_admin") and _is_private_host(host):
        return "Refuse : hote prive/interne (reserve aux administrateurs)"
    headers = args.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    body = args.get("body")
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.request(
                method, url, headers=headers,
                content=json.dumps(body) if isinstance(body, (dict, list)) else body,
            )
    except httpx.HTTPError as e:
        return f"Erreur HTTP : {e}"
    ctype = r.headers.get("content-type", "")
    text = _strip_html(r.text) if "html" in ctype else r.text
    return _truncate(f"[{r.status_code} {ctype.split(';')[0]}]\n{text}")


# --- Recherche dans la memoire vectorielle --------------------------------------

async def tool_memory_search(args: dict, ctx: dict) -> str:
    from src import memory  # import local pour eviter un cycle
    provider = ctx.get("provider")
    if not provider or not provider.get("embed_model"):
        return "Memoire indisponible (pas de modele d'embeddings sur le provider)"
    query = str(args.get("query", "")).strip()
    if not query:
        return "Requete vide"
    results = await memory.recall(provider, ctx.get("user_id"), query, k=6)
    if not results:
        return "Aucun souvenir pertinent"
    lines = []
    for m in results:
        who = "user" if m["role"] == "user" else "assistant"
        lines.append(f"- ({m['score']:.2f}) [{who}] {m['content'][:300]}")
    return "\n".join(lines)


# --- Execution Python (admin uniquement) ----------------------------------------

async def tool_python_exec(args: dict, ctx: dict) -> str:
    if not ctx.get("is_admin"):
        return "Refuse : outil python reserve aux administrateurs"
    code = str(args.get("code", ""))
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(WORKSPACE),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return _truncate(f"[exit {proc.returncode}]\n{out.decode(errors='replace')}")
    except asyncio.TimeoutError:
        proc.kill()
        return "Timeout (30s) — code interrompu"
    except OSError as e:
        return f"Erreur python : {e}"



async def tool_code_execute(args: dict, ctx: dict) -> str:
    """Execute du code Python dans un sandbox isole (tous users, timeout 10s).
    Acces reseau et modules systeme bloques, stdout/stderr captures."""
    code = str(args.get("code", "")).strip()
    if not code:
        return "Code vide"
    # Wrapper de securite : bloque les imports dangereux
    guard = (
        "import sys, builtins\n"
        "_blocked = {'os','subprocess','socket','shutil','importlib',"
        "'ctypes','multiprocessing','threading','signal','pty','fcntl','resource'}\n"
        "_orig_import = builtins.__import__\n"
        "def _safe_import(name, *a, **kw):\n"
        "    top = name.split('.')[0]\n"
        "    if top in _blocked:\n"
        "        raise ImportError(f'Import bloque (sandbox): {name}')\n"
        "    return _orig_import(name, *a, **kw)\n"
        "builtins.__import__ = _safe_import\n"
    )
    full_code = guard + "\n" + code
    try:
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as td:
            code_file = _os.path.join(td, "_code.py")
            with open(code_file, "w") as cf:
                cf.write(full_code)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", code_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=td,
                env={"PATH": "/usr/bin:/bin"},
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                return "Timeout (10s) — code interrompu"
        result = out.decode(errors="replace").strip()
        label = f"[exit {proc.returncode}]"
        return _truncate(f"{label}\n{result}" if result else f"{label} (aucune sortie)")
    except OSError as e:
        return f"Erreur sandbox : {e}"


# --- Wikipedia ------------------------------------------------------------------

WIKI_BASE = "https://fr.wikipedia.org"


async def tool_wikipedia(args: dict, ctx: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Requete vide"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            s = await client.get(f"{WIKI_BASE}/w/api.php", params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 3, "format": "json",
            })
            s.raise_for_status()
            hits = s.json().get("query", {}).get("search", [])
            if not hits:
                return "Aucun article trouve"
            title = hits[0]["title"]
            r = await client.get(f"{WIKI_BASE}/api/rest_v1/page/summary/{title}")
            r.raise_for_status()
            d = r.json()
    except httpx.HTTPError as e:
        return f"Erreur Wikipedia : {e}"
    out = [f"# {d.get('title', title)}", d.get("extract", ""),
           f"Source : {d.get('content_urls', {}).get('desktop', {}).get('page', '')}"]
    if len(hits) > 1:
        out.append("Autres articles : " + ", ".join(h["title"] for h in hits[1:]))
    return _truncate("\n".join(filter(None, out)))


# --- Meteo (Open-Meteo, sans cle) -----------------------------------------------

GEO_BASE = "https://geocoding-api.open-meteo.com"
METEO_BASE = "https://api.open-meteo.com"

_WMO = {0: "ciel clair", 1: "plutot clair", 2: "partiellement nuageux", 3: "couvert",
        45: "brouillard", 48: "brouillard givrant", 51: "bruine legere", 53: "bruine",
        55: "bruine dense", 61: "pluie legere", 63: "pluie", 65: "pluie forte",
        71: "neige legere", 73: "neige", 75: "neige forte", 80: "averses legeres",
        81: "averses", 82: "averses fortes", 95: "orage", 96: "orage avec grele",
        99: "orage violent avec grele"}


async def tool_weather(args: dict, ctx: dict) -> str:
    city = str(args.get("city", "")).strip()
    if not city:
        return "Ville manquante"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            g = await client.get(f"{GEO_BASE}/v1/search",
                                 params={"name": city, "count": 1, "language": "fr"})
            g.raise_for_status()
            results = g.json().get("results") or []
            if not results:
                return f"Ville introuvable : {city}"
            loc = results[0]
            f = await client.get(f"{METEO_BASE}/v1/forecast", params={
                "latitude": loc["latitude"], "longitude": loc["longitude"],
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "auto", "forecast_days": 3,
            })
            f.raise_for_status()
            d = f.json()
    except httpx.HTTPError as e:
        return f"Erreur meteo : {e}"
    cur = d.get("current", {})
    daily = d.get("daily", {})
    lines = [
        f"Meteo {loc['name']} ({loc.get('country', '')}) :",
        f"Actuellement : {cur.get('temperature_2m')}°C, "
        f"{_WMO.get(cur.get('weather_code'), 'n/a')}, vent {cur.get('wind_speed_10m')} km/h",
    ]
    for i, day in enumerate(daily.get("time", [])[:3]):
        lines.append(
            f"{day} : {daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}°C, "
            f"{_WMO.get(daily['weather_code'][i], '')}, "
            f"pluie {daily['precipitation_probability_max'][i]}%"
        )
    return "\n".join(lines)


# --- Flux RSS / Atom ------------------------------------------------------------

async def tool_rss_fetch(args: dict, ctx: dict) -> str:
    url = str(args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    max_items = min(int(args.get("max_items", 8) or 8), 20)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Maltai/1.1"})
            r.raise_for_status()
        root = ET.fromstring(r.content)
    except httpx.HTTPError as e:
        return f"Erreur flux : {e}"
    except ET.ParseError as e:
        return f"Flux illisible : {e}"
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")[:max_items] or root.findall(".//atom:entry", ns)[:max_items]
    if not items:
        return "Aucun element dans le flux"
    out = []
    for it in items:
        title = (it.findtext("title") or it.findtext("atom:title", namespaces=ns) or "").strip()
        link = it.findtext("link") or ""
        if not link:
            ln = it.find("atom:link", ns)
            link = ln.get("href") if ln is not None else ""
        date = (it.findtext("pubDate") or it.findtext("atom:updated", namespaces=ns) or "").strip()
        out.append(f"- {title}\n  {link}\n  {date}")
    return _truncate("\n".join(out))


# --- Transcription YouTube -------------------------------------------------------

def _yt_video_id(ref: str) -> str | None:
    ref = ref.strip()
    if re.fullmatch(r"[\w-]{11}", ref):
        return ref
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})", ref)
    return m.group(1) if m else None


async def tool_youtube_transcript(args: dict, ctx: dict) -> str:
    vid = _yt_video_id(str(args.get("url_or_id", "")))
    if not vid:
        return "URL/ID YouTube invalide"
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        loop = asyncio.get_event_loop()
        def fetch():
            api = YouTubeTranscriptApi()
            tr = api.fetch(vid, languages=["fr", "en"])
            return " ".join(seg.text for seg in tr)
        text = await loop.run_in_executor(None, fetch)
    except Exception as e:
        return f"Transcription indisponible : {e}"
    return _truncate(text)


# --- Generation d'images (endpoint compatible OpenAI) ----------------------------

async def tool_generate_image(args: dict, ctx: dict) -> str:
    from core.config import settings
    if not settings.IMAGE_API_BASE:
        return ("Generation d'images non configuree : definis IMAGE_API_BASE "
                "(+ IMAGE_API_KEY / IMAGE_MODEL) vers un endpoint compatible "
                "OpenAI /v1/images/generations (OpenAI, LocalAI, SD-webui, ComfyUI via wrapper...)")
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Prompt vide"
    size = str(args.get("size", "1024x1024"))
    base = settings.IMAGE_API_BASE.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    headers = {"Content-Type": "application/json"}
    if settings.IMAGE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.IMAGE_API_KEY}"
    payload = {"prompt": prompt, "size": size, "response_format": "b64_json", "n": 1}
    if settings.IMAGE_MODEL:
        payload["model"] = settings.IMAGE_MODEL
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{base}/images/generations", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json().get("data", [])
    except httpx.HTTPError as e:
        return f"Erreur generation image : {e}"
    if not data:
        return "Aucune image retournee"
    b64 = data[0].get("b64_json", "")
    if not b64:
        url = data[0].get("url", "")
        return f"Image generee (URL distante) : {url}" if url else "Reponse sans image"
    img_dir = WORKSPACE / "images"
    img_dir.mkdir(exist_ok=True)
    import uuid as _uuid
    path = img_dir / f"img_{_uuid.uuid4().hex[:10]}.png"
    path.write_bytes(base64.b64decode(b64))
    rel = path.relative_to(WORKSPACE)
    return (f"Image generee : {rel} ({path.stat().st_size // 1024} Ko). "
            f"Telechargeable dans Reglages > Workspace de l'agent.")


# --- Deep research ----------------------------------------------------------------

RESEARCH_MAX_CHARS = 12000


async def tool_deep_research(args: dict, ctx: dict) -> str:
    """Recherche approfondie : plan de requetes -> recherches -> lecture des
    meilleures pages -> rapport markdown synthetise par le modele."""
    from src import llm as _llm
    provider = ctx.get("provider")
    model = ctx.get("model")
    if not provider or not model:
        return "Deep research indisponible (provider/modele manquant)"
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return "Sujet vide"

    async def ask(prompt: str) -> str:
        try:
            return await _llm.complete(
                provider["base_url"], provider["api_key"], model,
                [{"role": "user", "content": prompt}],
            )
        except _llm.LLMError as e:
            raise RuntimeError(str(e))

    try:
        # 1. Plan : 3 requetes de recherche
        plan = await ask(
            f"Sujet de recherche : {topic}\n"
            "Donne exactement 3 requetes de recherche web courtes (3-6 mots), "
            "une par ligne, sans numerotation ni autre texte."
        )
        queries = [q.strip("-• ").strip() for q in plan.splitlines() if q.strip()][:3] or [topic]

        # 2. Recherches
        findings = []
        urls: list[str] = []
        for q in queries:
            res = await tool_web_search({"query": q}, ctx)
            findings.append(f"### Recherche : {q}\n{res}")
            urls += re.findall(r"https?://\S+", res)

        # 3. Lecture des 3 premieres pages distinctes
        seen, picked = set(), []
        for u in urls:
            host = u.split("/")[2] if "//" in u else u
            if host not in seen:
                seen.add(host)
                picked.append(u)
            if len(picked) >= 3:
                break
        pages = []
        for u in picked:
            content = await tool_web_fetch({"url": u}, ctx)
            pages.append(f"### Source : {u}\n{content[:3000]}")

        # 4. Synthese
        corpus = "\n\n".join(findings + pages)[:16000]
        report = await ask(
            f"Sujet : {topic}\n\nDonnees collectees :\n{corpus}\n\n"
            "Redige un rapport structure en markdown (titres ##, points cles, "
            "chiffres si disponibles) en francais, puis une section 'Sources' "
            "listant les URLs utilisees. Sois factuel et concis."
        )
    except RuntimeError as e:
        return f"Erreur deep research : {e}"
    if len(report) > RESEARCH_MAX_CHARS:
        report = report[:RESEARCH_MAX_CHARS] + "\n…[tronque]"
    return report


# --- Notes & taches (inspire d'Odysseus) ------------------------------------

def _fmt_ts(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _find_note(conn, user_id, id_prefix: str, kind: str):
    rows = conn.execute(
        "SELECT * FROM notes WHERE kind=? AND (user_id IS ? OR user_id=?) "
        "AND id LIKE ? ORDER BY created_at",
        (kind, user_id, user_id, id_prefix + "%"),
    ).fetchall()
    return rows


async def tool_note_add(args: dict, ctx: dict) -> str:
    from core import database as db
    content = str(args.get("content", "")).strip()
    if not content:
        return "Contenu vide"
    conn = db.connect()
    try:
        nid = db.new_id()
        conn.execute(
            "INSERT INTO notes (id, user_id, kind, content, done, created_at) "
            "VALUES (?, ?, 'note', ?, 0, ?)",
            (nid, ctx.get("user_id"), content, db.now()),
        )
        conn.commit()
        return f"Note enregistree (id {nid[:8]})"
    finally:
        conn.close()


async def tool_note_list(args: dict, ctx: dict) -> str:
    from core import database as db
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM notes WHERE kind='note' AND (user_id IS ? OR user_id=?) "
            "ORDER BY created_at DESC LIMIT 50",
            (ctx.get("user_id"), ctx.get("user_id")),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "Aucune note"
    lines = [f"- [{r['id'][:8]}] ({_fmt_ts(r['created_at'])}) {r['content']}" for r in rows]
    return _truncate("\n".join(lines))


async def tool_note_delete(args: dict, ctx: dict) -> str:
    from core import database as db
    id_prefix = str(args.get("note_id", "")).strip()
    if not id_prefix:
        return "note_id manquant"
    conn = db.connect()
    try:
        rows = _find_note(conn, ctx.get("user_id"), id_prefix, "note")
        if not rows:
            return f"Aucune note avec l'id {id_prefix}"
        if len(rows) > 1:
            return f"Id ambigu ({len(rows)} notes). Precise plus de caracteres."
        conn.execute("DELETE FROM notes WHERE id=?", (rows[0]["id"],))
        conn.commit()
        return f"Note supprimee : {rows[0]['content'][:80]}"
    finally:
        conn.close()


async def tool_todo_add(args: dict, ctx: dict) -> str:
    from core import database as db
    content = str(args.get("content", "")).strip()
    if not content:
        return "Contenu vide"
    conn = db.connect()
    try:
        nid = db.new_id()
        conn.execute(
            "INSERT INTO notes (id, user_id, kind, content, done, created_at) "
            "VALUES (?, ?, 'todo', ?, 0, ?)",
            (nid, ctx.get("user_id"), content, db.now()),
        )
        conn.commit()
        return f"Tache ajoutee (id {nid[:8]})"
    finally:
        conn.close()


async def tool_todo_list(args: dict, ctx: dict) -> str:
    from core import database as db
    include_done = bool(args.get("include_done", False))
    conn = db.connect()
    try:
        sql = ("SELECT * FROM notes WHERE kind='todo' AND (user_id IS ? OR user_id=?) "
               + ("" if include_done else "AND done=0 ")
               + "ORDER BY done, created_at DESC LIMIT 100")
        rows = conn.execute(sql, (ctx.get("user_id"), ctx.get("user_id"))).fetchall()
    finally:
        conn.close()
    if not rows:
        return "Aucune tache" + (" (toutes terminees ?)" if not include_done else "")
    lines = []
    for r in rows:
        box = "[x]" if r["done"] else "[ ]"
        lines.append(f"- {box} [{r['id'][:8]}] {r['content']}")
    return _truncate("\n".join(lines))


async def tool_todo_done(args: dict, ctx: dict) -> str:
    from core import database as db
    id_prefix = str(args.get("todo_id", "")).strip()
    if not id_prefix:
        return "todo_id manquant"
    conn = db.connect()
    try:
        rows = _find_note(conn, ctx.get("user_id"), id_prefix, "todo")
        if not rows:
            return f"Aucune tache avec l'id {id_prefix}"
        if len(rows) > 1:
            return f"Id ambigu ({len(rows)} taches). Precise plus de caracteres."
        conn.execute("UPDATE notes SET done=1 WHERE id=?", (rows[0]["id"],))
        conn.commit()
        return f"Tache terminee : {rows[0]['content'][:80]}"
    finally:
        conn.close()


# --- Envoi d'email (SMTP, inspire d'Odysseus) --------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def tool_email_send(args: dict, ctx: dict) -> str:
    from core.config import settings
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return ("Email non configure. Definis les variables d'environnement "
                "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (et SMTP_FROM optionnel).")
    to = str(args.get("to", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not _EMAIL_RE.match(to):
        return f"Adresse destinataire invalide : {to}"
    if not subject or not body:
        return "subject et body sont requis"

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    def send():
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
            if settings.SMTP_TLS:
                s.starttls()
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)

    try:
        await asyncio.to_thread(send)
        return f"Email envoye a {to} (sujet : {subject})"
    except Exception as e:
        return f"Echec de l'envoi : {e}"


# =============================================================================
# Pack v1.3 — memory_save, session_search, patch_file, skill_save/list/run
# =============================================================================

async def tool_memory_save(args: dict, ctx: dict) -> str:
    """Ecrit un fait dans la memoire vectorielle persistante de l'utilisateur."""
    from core import database as db
    from src.memory import embed_text, cosine_sim  # noqa: F401
    import struct, numpy as np

    fact = str(args.get("fact", "")).strip()
    if not fact:
        return "Erreur : parametre 'fact' manquant."
    user_id = ctx.get("user_id")
    session_id = ctx.get("session_id")
    provider = ctx.get("provider")
    if provider is None:
        return "Pas de provider configure, impossible de calculer l'embedding."
    try:
        emb = await embed_text(fact, provider)
    except Exception as e:
        return f"Erreur embedding : {e}"
    dim = len(emb)
    blob = struct.pack(f"{dim}f", *emb)
    mid = db.add_memory(user_id, session_id, "user", fact, blob, dim)
    return f"Souvenir enregistre (id={mid[:8]}…) : « {fact[:80]} »"


async def tool_session_search(args: dict, ctx: dict) -> str:
    """Recherche plein texte dans toutes les conversations (FTS5)."""
    from core import database as db

    query = str(args.get("query", "")).strip()
    if not query:
        return "Erreur : parametre 'query' manquant."
    limit = min(int(args.get("limit", 8)), 20)
    user_id = ctx.get("user_id")
    results = db.fts_search(query, user_id, limit)
    if not results:
        return f"Aucun resultat pour « {query} »."
    if results and "error" in results[0]:
        return f"Erreur FTS : {results[0]['error']}"
    lines = [f"Resultats pour « {query} » ({len(results)}) :\n"]
    for r in results:
        import datetime
        ts = datetime.datetime.fromtimestamp(r["updated_at"]).strftime("%d/%m/%Y")
        lines.append(f"- [{r['session_title']}] ({ts}) [{r['role']}] {r['snippet']}")
    return "\n".join(lines)


async def tool_patch_file(args: dict, ctx: dict) -> str:
    """Remplace un bloc de texte dans un fichier workspace par un nouveau contenu."""
    path_str = str(args.get("path", "")).strip()
    old_text = args.get("old_str", "")
    new_text = args.get("new_str", "")
    if not path_str:
        return "Erreur : parametre 'path' manquant."
    try:
        target = _safe_path(path_str, ctx)
    except ValueError as e:
        return str(e)
    if not target.exists():
        return f"Fichier introuvable : {path_str}"
    content = target.read_text(encoding="utf-8")
    if old_text and old_text not in content:
        return "Erreur : le bloc 'old_str' est introuvable dans le fichier."
    if old_text:
        content = content.replace(old_text, new_text, 1)
    else:
        content = new_text
    target.write_text(content, encoding="utf-8")
    return f"Fichier mis a jour : {path_str} ({len(new_text)} caracteres inseres)"


async def tool_skill_save(args: dict, ctx: dict) -> str:
    """Sauvegarde une procedure reutilisable (skill) dans la base."""
    from core import database as db

    name = str(args.get("name", "")).strip().lower().replace(" ", "_")
    description = str(args.get("description", "")).strip()
    body = str(args.get("body", "")).strip()
    if not name or not body:
        return "Erreur : 'name' et 'body' sont requis."
    user_id = ctx.get("user_id")
    db.skill_save(user_id, name, description, body)
    return f"Skill « {name} » sauvegarde."


async def tool_skill_list(args: dict, ctx: dict) -> str:
    """Liste les skills disponibles pour cet utilisateur."""
    from core import database as db

    user_id = ctx.get("user_id")
    skills = db.list_skills(user_id)
    if not skills:
        return "Aucun skill enregistre. Utilisez skill_save pour en creer un."
    lines = ["Skills disponibles :\n"]
    for s in skills:
        desc = f" — {s['description']}" if s['description'] else ""
        lines.append(f"• **{s['name']}**{desc}")
    return "\n".join(lines)


async def tool_skill_run(args: dict, ctx: dict) -> str:
    """Rappelle le corps d'un skill et le retourne pour que l'agent l'execute."""
    from core import database as db

    name = str(args.get("name", "")).strip().lower().replace(" ", "_")
    if not name:
        return "Erreur : 'name' est requis."
    user_id = ctx.get("user_id")
    skill = db.get_skill(user_id, name)
    if not skill:
        return f"Skill « {name} » introuvable. Verifiez le nom avec skill_list."
    return f"=== Skill : {skill['name']} ===\n{skill['body']}"


# =============================================================================
# Pack v1.4 — image_generate, page_summary
# =============================================================================

async def tool_image_generate(args: dict, ctx: dict) -> str:
    """Genere une image via l'API images du provider OpenAI-compatible."""
    provider = ctx.get("provider")
    if not provider:
        return "Erreur : aucun provider configure."
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Erreur : parametre 'prompt' manquant."
    size = str(args.get("size", "1024x1024"))
    quality = str(args.get("quality", "standard"))
    n = int(args.get("n", 1))
    base_url = provider["base_url"].rstrip("/")
    # Remove /v1 suffix if already present to reconstruct cleanly
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    url = f"{base_url}/v1/images/generations"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    payload = {"prompt": prompt, "n": n, "size": size}
    # quality param only supported by some providers (dall-e-3)
    if quality != "standard":
        payload["quality"] = quality
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            return f"Erreur API images ({r.status_code}) : {r.text[:300]}"
        data = r.json()
        images = data.get("data", [])
        if not images:
            return "Aucune image retournee par le provider."
        urls = []
        for img in images:
            if img.get("url"):
                urls.append(img["url"])
            elif img.get("b64_json"):
                urls.append(f"data:image/png;base64,{img['b64_json'][:40]}…")
        return "Image(s) generee(s) :\n" + "\n".join(urls)
    except httpx.HTTPError as e:
        return f"Erreur reseau : {e}"


async def tool_page_summary(args: dict, ctx: dict) -> str:
    """Recupere le contenu d'une page web et en extrait le texte principal."""
    import re as _re
    url = str(args.get("url", "")).strip()
    if not url or not url.startswith(("http://", "https://")):
        return "Erreur : URL invalide ou manquante."
    # SSRF protection : bloquer IPs privees / localhost
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if _is_private_host(host):
            return "Refuse : l'acces aux adresses privees/locales est interdit."
    except Exception:
        return "Erreur : impossible de resoudre l'URL."
    question = str(args.get("question", "")).strip()
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MaltaiBot/1.0)"},
        ) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return f"Erreur HTTP {r.status_code} sur {url}"
        raw = r.text
    except httpx.HTTPError as e:
        return f"Erreur reseau : {e}"

    # Strip scripts, styles, HTML tags
    raw = _re.sub(r"<(script|style|head)[^>]*>.*?</(script|style|head)>", " ", raw, flags=_re.S | _re.I)
    raw = _re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = _re.sub(r"[ \t]{2,}", " ", raw)
    raw = _re.sub(r"\n{3,}", "\n\n", raw).strip()

    # Truncate to ~6000 chars for context
    excerpt = raw[:6000]
    if len(raw) > 6000:
        excerpt += f"\n\n[... {len(raw)-6000} caracteres supprimes ...]"

    if question:
        return f"Page : {url}\nQuestion : {question}\n\nContenu :\n{excerpt}"
    return f"Page : {url}\n\nContenu extrait :\n{excerpt}"

# --- Registre ----------------------------------------------------------------

Tool = dict[str, Any]

TOOLS: dict[str, dict] = {
    "calculator": {
        "run": tool_calculator,
        "spec": {
            "name": "calculator",
            "description": "Evalue une expression arithmetique (+ - * / // % **).",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "Ex: (12*7)+3**2"}},
                "required": ["expression"],
            },
        },
    },
    "list_files": {
        "run": tool_list_files,
        "spec": {
            "name": "list_files",
            "description": "Liste les fichiers du workspace de l'agent.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Sous-dossier (defaut: racine)"}},
            },
        },
    },
    "read_file": {
        "run": tool_read_file,
        "spec": {
            "name": "read_file",
            "description": "Lit un fichier texte du workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "run": tool_write_file,
        "spec": {
            "name": "write_file",
            "description": "Ecrit (ou ecrase) un fichier texte dans le workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "web_search": {
        "run": tool_web_search,
        "spec": {
            "name": "web_search",
            "description": "Recherche web (DuckDuckGo). Retourne titres, URLs et extraits.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "browser_navigate": {
        "run": tool_browser_navigate,
        "spec": {
            "name": "browser_navigate",
            "description": "Ouvre une page web dans le navigateur texte de Maltai et garde son etat pour les outils browser_*.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL http/https a ouvrir"}},
                "required": ["url"],
            },
        },
    },
    "browser_snapshot": {
        "run": tool_browser_snapshot,
        "spec": {
            "name": "browser_snapshot",
            "description": "Retourne un snapshot lisible de la page ouverte : titre, texte, liens et formulaires.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL optionnelle a ouvrir avant le snapshot"}},
            },
        },
    },
    "browser_links": {
        "run": tool_browser_links,
        "spec": {
            "name": "browser_links",
            "description": "Liste les liens trouves sur la page actuellement ouverte par browser_navigate.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "browser_form_list": {
        "run": tool_browser_form_list,
        "spec": {
            "name": "browser_form_list",
            "description": "Liste les formulaires de la page ouverte : index, methode, action et champs.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL optionnelle a ouvrir avant de lister les formulaires"}},
            },
        },
    },
    "browser_submit": {
        "run": tool_browser_submit,
        "spec": {
            "name": "browser_submit",
            "description": "Soumet un formulaire simple de la page ouverte via GET ou POST. N'execute pas de JavaScript.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Index du formulaire, defaut 0"},
                    "data": {"type": "object", "description": "Champs a envoyer"},
                    "method": {"type": "string", "description": "GET ou POST optionnel"},
                    "action": {"type": "string", "description": "URL action optionnelle"},
                },
            },
        },
    },
    "web_fetch": {
        "run": tool_web_fetch,
        "spec": {
            "name": "web_fetch",
            "description": "Recupere le contenu texte d'une page web (URL exacte).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "get_datetime": {
        "run": tool_get_datetime,
        "spec": {
            "name": "get_datetime",
            "description": "Donne la date et l'heure actuelles (fuseau du serveur).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "http_request": {
        "run": tool_http_request,
        "spec": {
            "name": "http_request",
            "description": "Requete HTTP generique vers une API publique (GET/POST/PUT/PATCH/DELETE). Hotes prives interdits sauf admin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "description": "GET par defaut"},
                    "headers": {"type": "object", "description": "En-tetes optionnels"},
                    "body": {"description": "Corps JSON ou texte pour POST/PUT/PATCH"},
                },
                "required": ["url"],
            },
        },
    },
    "memory_search": {
        "run": tool_memory_search,
        "spec": {
            "name": "memory_search",
            "description": "Recherche dans la memoire vectorielle des conversations passees de l'utilisateur.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "wikipedia": {
        "run": tool_wikipedia,
        "spec": {
            "name": "wikipedia",
            "description": "Cherche un article Wikipedia (fr) et retourne son resume.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    "weather": {
        "run": tool_weather,
        "spec": {
            "name": "weather",
            "description": "Meteo actuelle + previsions 3 jours pour une ville (Open-Meteo, sans cle).",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        },
    },
    "rss_fetch": {
        "run": tool_rss_fetch,
        "spec": {
            "name": "rss_fetch",
            "description": "Lit un flux RSS/Atom et retourne les derniers articles (titre, lien, date).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "max_items": {"type": "integer"}},
                "required": ["url"],
            },
        },
    },
    "youtube_transcript": {
        "run": tool_youtube_transcript,
        "spec": {
            "name": "youtube_transcript",
            "description": "Recupere la transcription d'une video YouTube (fr puis en).",
            "parameters": {"type": "object", "properties": {"url_or_id": {"type": "string"}}, "required": ["url_or_id"]},
        },
    },
    "generate_image": {
        "run": tool_generate_image,
        "spec": {
            "name": "generate_image",
            "description": "Genere une image (endpoint compatible OpenAI configure via IMAGE_API_BASE) et la sauve dans le workspace.",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}, "size": {"type": "string", "description": "ex: 1024x1024"}},
                "required": ["prompt"],
            },
        },
    },
    "deep_research": {
        "run": tool_deep_research,
        "spec": {
            "name": "deep_research",
            "description": "Recherche web approfondie multi-etapes sur un sujet -> rapport markdown structure avec sources. Plus long qu'une simple recherche.",
            "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
        },
    },
    "python_exec": {
        "run": tool_python_exec,
        "spec": {
            "name": "python_exec",
            "description": "Execute du code Python dans le workspace (ADMIN seulement, timeout 30s, mode isole -I).",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    "code_execute": {
        "run": tool_code_execute,
        "spec": {
            "name": "code_execute",
            "description": "Execute du code Python dans un sandbox isole (disponible pour tous). Utile pour calculs, transformations de donnees, generation de texte programme. Timeout 10s. Acces reseau et systeme bloques.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code Python a executer"},
                },
                "required": ["code"],
            },
        },
    },
    "shell": {
        "run": tool_shell,
        "spec": {
            "name": "shell",
            "description": "Execute une commande shell dans le workspace (ADMIN seulement, timeout 30s).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "note_add": {
        "run": tool_note_add,
        "spec": {
            "name": "note_add",
            "description": "Enregistre une note persistante pour l'utilisateur (memo, idee, information a retenir).",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Texte de la note"}},
                "required": ["content"],
            },
        },
    },
    "note_list": {
        "run": tool_note_list,
        "spec": {
            "name": "note_list",
            "description": "Liste les notes enregistrees de l'utilisateur (avec leur id).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "note_delete": {
        "run": tool_note_delete,
        "spec": {
            "name": "note_delete",
            "description": "Supprime une note par son id (prefixe accepte).",
            "parameters": {
                "type": "object",
                "properties": {"note_id": {"type": "string", "description": "Id (ou debut d'id) de la note"}},
                "required": ["note_id"],
            },
        },
    },
    "todo_add": {
        "run": tool_todo_add,
        "spec": {
            "name": "todo_add",
            "description": "Ajoute une tache a la todo-list persistante de l'utilisateur.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Description de la tache"}},
                "required": ["content"],
            },
        },
    },
    "todo_list": {
        "run": tool_todo_list,
        "spec": {
            "name": "todo_list",
            "description": "Liste les taches de la todo-list (en cours par defaut).",
            "parameters": {
                "type": "object",
                "properties": {"include_done": {"type": "boolean", "description": "Inclure les taches terminees"}},
            },
        },
    },
    "todo_done": {
        "run": tool_todo_done,
        "spec": {
            "name": "todo_done",
            "description": "Marque une tache comme terminee par son id (prefixe accepte).",
            "parameters": {
                "type": "object",
                "properties": {"todo_id": {"type": "string", "description": "Id (ou debut d'id) de la tache"}},
                "required": ["todo_id"],
            },
        },
    },
    "email_send": {
        "run": tool_email_send,
        "spec": {
            "name": "email_send",
            "description": "Envoie un email (texte) via SMTP. Necessite SMTP_HOST/SMTP_USER/SMTP_PASSWORD en variables d'environnement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Adresse du destinataire"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Corps du message (texte brut)"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    # --- v1.3 ----------------------------------------------------------------
    "memory_save": {
        "run": tool_memory_save,
        "spec": {
            "name": "memory_save",
            "description": "Memorise durablement un fait important dans la memoire vectorielle persistante.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "Le fait a memoriser (phrase courte et precise)"},
                },
                "required": ["fact"],
            },
        },
    },
    "session_search": {
        "run": tool_session_search,
        "spec": {
            "name": "session_search",
            "description": "Recherche plein texte (FTS5) dans toutes les conversations passees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Termes de recherche"},
                    "limit": {"type": "integer", "description": "Nombre max de resultats (defaut 8, max 20)"},
                },
                "required": ["query"],
            },
        },
    },
    "patch_file": {
        "run": tool_patch_file,
        "spec": {
            "name": "patch_file",
            "description": "Remplace un bloc de texte dans un fichier du workspace par un nouveau contenu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Chemin relatif du fichier dans le workspace"},
                    "old_str": {"type": "string", "description": "Texte exact a remplacer (laisser vide pour ecraser tout le fichier)"},
                    "new_str": {"type": "string", "description": "Nouveau texte"},
                },
                "required": ["path", "new_str"],
            },
        },
    },
    "skill_save": {
        "run": tool_skill_save,
        "spec": {
            "name": "skill_save",
            "description": "Sauvegarde une procedure reutilisable (skill) en base. Permet a l'agent de retrouver ses methodes de travail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Identifiant court du skill (ex: redaction_email)"},
                    "description": {"type": "string", "description": "Courte description du skill"},
                    "body": {"type": "string", "description": "Corps de la procedure (texte libre, instructions pas-a-pas)"},
                },
                "required": ["name", "body"],
            },
        },
    },
    "skill_list": {
        "run": tool_skill_list,
        "spec": {
            "name": "skill_list",
            "description": "Liste tous les skills disponibles pour cet utilisateur.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "skill_run": {
        "run": tool_skill_run,
        "spec": {
            "name": "skill_run",
            "description": "Rappelle le corps d'un skill sauvegarde pour l'executer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nom du skill a executer"},
                },
                "required": ["name"],
            },
        },
    },

    "image_generate": {
        "run": tool_image_generate,
        "spec": {
            "name": "image_generate",
            "description": "Genere une image a partir d'un prompt textuel via le provider OpenAI-compatible configure (DALL-E, etc.). Retourne l'URL de l'image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Description precise de l'image a generer"},
                    "size": {"type": "string", "description": "Taille : 256x256 | 512x512 | 1024x1024 | 1792x1024 | 1024x1792", "default": "1024x1024"},
                    "quality": {"type": "string", "description": "standard ou hd (DALL-E 3 uniquement)", "default": "standard"},
                    "n": {"type": "integer", "description": "Nombre d'images (1-4)", "default": 1},
                },
                "required": ["prompt"],
            },
        },
    },
    "page_summary": {
        "run": tool_page_summary,
        "spec": {
            "name": "page_summary",
            "description": "Recupere et extrait le contenu textuel d'une page web a partir de son URL. Utile pour lire un article, une documentation ou repondre a une question sur un site.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL complete de la page (https://...)"},
                    "question": {"type": "string", "description": "Question optionnelle sur le contenu de la page"},
                },
                "required": ["url"],
            },
        },
    },
}


def openai_tool_specs(is_admin: bool, plan: str = "premium") -> list[dict]:
    """Specs au format OpenAI selon le plan."""
    specs = []
    for name, t in TOOLS.items():
        if not plans.tool_allowed(name, plan, is_admin):
            continue
        specs.append({"type": "function", "function": t["spec"]})
    return specs


async def execute_tool(name: str, args: dict, ctx: dict) -> str:
    tool = TOOLS.get(name)
    if not tool:
        return f"Outil inconnu : {name}"
    if not plans.tool_allowed(name, ctx.get("plan"), bool(ctx.get("is_admin"))):
        return "Premium requis pour utiliser les outils de l'agent."
    runner: Callable[[dict, dict], Awaitable[str]] = tool["run"]
    try:
        return await runner(args, ctx)
    except Exception as e:  # garde-fou : un outil ne doit jamais tuer la boucle
        return f"Erreur outil {name} : {e}"
