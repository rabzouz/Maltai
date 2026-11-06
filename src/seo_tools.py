"""Agent SEO / SEO local / GEO pour Maltai.

Outils exposes a l'agent (enregistres dans src.tools.TOOLS) :
  - seo_site_audit    : audit technique multi-pages (facon Seobility)
  - local_seo_audit   : SEO local (NAP, LocalBusiness, carte, horaires, avis)
  - geo_audit         : Generative Engine Optimization (bots IA, llms.txt,
                        donnees structurees, contenu citable, entite)
  - schema_generate   : generateur de JSON-LD schema.org
  - llms_txt_generate : generateur de llms.txt a partir d'un crawl
  - seo_keywords      : extraction de mots-cles + ecart concurrent

Toutes les requetes passent par src.tools._safe_fetch (anti-SSRF, redirections
revalidees). Les rapports complets sont enregistres dans le workspace
utilisateur (dossier seo/), la reponse a l'agent reste compacte.
"""
from __future__ import annotations

import html
import json
import re
import socket
from collections import Counter
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

UA = "Mozilla/5.0 (compatible; MaltaiSEO/1.0; +https://maltai.fr)"
SKIP_EXT = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".zip",
    ".css", ".js", ".xml", ".json", ".mp4", ".mp3", ".webm", ".woff", ".woff2",
    ".ttf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)
SEVERITY_WEIGHT = {"critical": 15, "important": 7, "minor": 2}
SEVERITY_LABEL = {"critical": "P0", "important": "P1", "minor": "P2"}


def _t():
    """Acces paresseux a src.tools (evite l'import circulaire)."""
    from src import tools
    return tools


# --- Utilitaires HTML / URL ---------------------------------------------------

_ATTR_RE = re.compile(
    r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s"\'>]+))'
)
_HIDDEN_RE = re.compile(r"<(noscript|svg|template)\b.*?</\1>", re.I | re.S)
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        val = m.group(3) if m.group(3) is not None else (
            m.group(4) if m.group(4) is not None else (m.group(5) or ""))
        out[m.group(1).lower()] = html.unescape(val)
    return out


def _clean_text(fragment: str) -> str:
    return _t()._strip_html(fragment or "")


def _visible_text(raw: str) -> str:
    return _clean_text(_HIDDEN_RE.sub(" ", raw or ""))


def _metas(raw: str) -> list[dict[str, str]]:
    return [_attrs(tag) for tag in re.findall(r"<meta\b[^>]*>", raw, re.I)]


def _meta_value(metas: list[dict[str, str]], key: str) -> str:
    key = key.lower()
    for a in metas:
        if a.get("name", "").lower() == key or a.get("property", "").lower() == key:
            return (a.get("content") or "").strip()
    return ""


def _link_tags(raw: str) -> list[dict[str, str]]:
    return [_attrs(tag) for tag in re.findall(r"<link\b[^>]*>", raw, re.I)]


def _norm_url(url: Any) -> str:
    url = str(url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    return url


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _same_site(url: str, host: str) -> bool:
    return _strip_www(_host(url)) == _strip_www(host)


def _resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except (socket.gaierror, UnicodeError):
        return False


def _anchors(raw: str, base_url: str) -> list[dict[str, str]]:
    out = []
    for m in _ANCHOR_RE.finditer(raw):
        a = _attrs(m.group(1))
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        text = _clean_text(m.group(2))[:120]
        if not text:
            imgs = re.findall(r"<img\b[^>]*>", m.group(2), re.I)
            text = _attrs(imgs[0]).get("alt", "") if imgs else ""
        out.append({
            "href": href,
            "url": href if href.startswith(("mailto:", "tel:")) else urljoin(base_url, href).split("#")[0],
            "text": text,
            "rel": a.get("rel", ""),
        })
    return out


def _ld_nodes(raw: str) -> list[dict]:
    items = _t()._extract_json_ld(raw, limit=25)
    nodes: list[dict] = []

    def walk(x: Any) -> None:
        # Noeuds de premier niveau et membres de @graph uniquement : les
        # sous-objets (PostalAddress, Offer, Answer...) ne sont pas des entites.
        if isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            if "@graph" in x:
                walk(x["@graph"])
            if "@type" in x:
                nodes.append(x)

    walk(items)
    return nodes


def _types(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t if x]
    return [str(t)] if t else []


def _grade(score: int) -> str:
    return "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 45 else "E" if score >= 30 else "F"


def _score_from(issues: list[dict]) -> int:
    return max(0, 100 - sum(SEVERITY_WEIGHT.get(i["severity"], 2) for i in issues))


def _issue(severity: str, message: str, fix: str = "", **extra: Any) -> dict:
    d = {"severity": severity, "priority": SEVERITY_LABEL[severity], "message": message}
    if fix:
        d["fix"] = fix
    d.update(extra)
    return d


def _sort_issues(issues: list[dict]) -> list[dict]:
    order = {"critical": 0, "important": 1, "minor": 2}
    return sorted(issues, key=lambda i: order.get(i["severity"], 3))


# --- HTTP ------------------------------------------------------------------------

async def _get(url: str, ctx: dict, timeout: float = 20):
    return await _t()._safe_fetch("GET", url, ctx, timeout=timeout, user_agent=UA)


async def _probe(url: str, ctx: dict, timeout: float = 10) -> dict:
    """Une seule requete SANS suivre les redirections (pour tester 301/www/https)."""
    T = _t()
    host = _host(url)
    if not host or not _resolves(host):
        return {"url": url, "error": "dns", "detail": "nom de domaine non resolu"}
    if not (ctx or {}).get("is_admin") and T._is_private_host(host):
        return {"url": url, "error": "private", "detail": "hote interne refuse"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r = await c.get(url, headers={"User-Agent": UA})
        return {"url": url, "status": r.status_code, "location": r.headers.get("location", "")}
    except httpx.ConnectError as e:
        msg = str(e)
        kind = "cert" if ("CERTIFICATE" in msg.upper() or "SSL" in msg.upper()) else "connect"
        return {"url": url, "error": kind, "detail": msg[:160]}
    except httpx.HTTPError as e:
        return {"url": url, "error": "http", "detail": str(e)[:160]}


# --- robots.txt ------------------------------------------------------------------

def _robots_parse(txt: str) -> tuple[list[dict], list[str]]:
    groups: list[dict] = []
    sitemaps: list[str] = []
    cur: dict | None = None
    last_ua = False
    for line in (txt or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if cur is None or not last_ua:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(v.lower())
            last_ua = True
        elif k in ("allow", "disallow"):
            if cur is None:
                cur = {"agents": ["*"], "rules": []}
                groups.append(cur)
            cur["rules"].append((k, v))
            last_ua = False
        else:
            if k == "sitemap" and v:
                sitemaps.append(v)
            last_ua = False
    return groups, sitemaps


def _robots_group(groups: list[dict], ua: str) -> dict | None:
    ua = ua.lower()
    best, best_len = None, -1
    for g in groups:
        for a in g["agents"]:
            if a != "*" and a and a in ua and len(a) > best_len:
                best, best_len = g, len(a)
    if best:
        return best
    for g in groups:
        if "*" in g["agents"]:
            return g
    return None


def _rule_match(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return re.match(rx, path) is not None


def _robots_allowed(groups: list[dict], ua: str, path: str = "/") -> bool:
    g = _robots_group(groups, ua)
    if not g:
        return True
    best, blen = None, -1
    for kind, pat in g["rules"]:
        if _rule_match(pat, path):
            if len(pat) > blen or (len(pat) == blen and kind == "allow"):
                best, blen = kind, len(pat)
    return best != "disallow"


async def _fetch_robots(origin: str, ctx: dict) -> dict:
    try:
        r = await _get(origin + "/robots.txt", ctx, timeout=15)
        ok = r.status_code == 200 and "<html" not in r.text[:300].lower()
        groups, sitemaps = _robots_parse(r.text if ok else "")
        return {"status": r.status_code, "present": ok, "groups": groups,
                "sitemaps": sitemaps, "text": r.text[:3000] if ok else ""}
    except (httpx.HTTPError, PermissionError) as e:
        return {"status": None, "present": False, "groups": [], "sitemaps": [], "error": str(e)[:160]}


# --- Analyse d'une page --------------------------------------------------------------

_STOP = set("""
a à au aux avec ce ces cet cette ci comme dans de des du donc elle elles en encore et eu
il ils je la le les leur leurs lui ma mais me mes moi mon ne ni nos notre nous on ou où par
pas plus pour qu que qui sa sans se ses si son sont sur ta te tes toi ton tu un une vos
votre vous y été être est sont ont avez avons fait faire peut tout tous toute toutes
très aussi bien même ainsi alors chez entre vers sous depuis selon dont car leur cela ça
d l j n s t c m qu's
the and for with that this from your you are was were has have had not but all can its
our they them their will would there what when which who how why into over than then
also just more most other some such only very about after before here out off use using
of to in on is it an as at be by do if no or so up we
""".split())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9à-ÿœæç]+(?:-[a-z0-9à-ÿœæç]+)*", (text or "").lower())


def _page_report(raw: str, url: str, status: int = 200, headers: Any = None) -> dict:
    T = _t()
    metas = _metas(raw)
    title = T._html_title(raw)
    desc = _meta_value(metas, "description")
    robots_meta = _meta_value(metas, "robots").lower()
    canonical, hreflang = "", []
    for lk in _link_tags(raw):
        rels = lk.get("rel", "").lower().split()
        if "canonical" in rels and lk.get("href"):
            canonical = urljoin(url, lk["href"])
        if "alternate" in rels and lk.get("hreflang"):
            hreflang.append({"lang": lk["hreflang"], "href": urljoin(url, lk.get("href", ""))})
    heads = [(int(lv), _clean_text(txt)) for lv, txt in _HEADING_RE.findall(raw)]
    h1 = [t for lv, t in heads if lv == 1]
    h2 = [t for lv, t in heads if lv == 2]
    text = _visible_text(raw)
    toks = _tokens(text)
    words = len(toks)
    imgs = [_attrs(t) for t in re.findall(r"<img\b[^>]*>", raw, re.I)]
    missing_alt = [i.get("src", "")[:120] for i in imgs if "alt" not in i]
    host = _host(url)
    anchors = _anchors(raw, url)
    internal = [a for a in anchors if a["url"].startswith("http") and _same_site(a["url"], host)]
    external = [a for a in anchors if a["url"].startswith("http") and not _same_site(a["url"], host)]
    html_tag = re.search(r"<html\b([^>]*)>", raw, re.I)
    lang = _attrs(html_tag.group(1)).get("lang", "") if html_tag else ""
    ld = _ld_nodes(raw)
    ld_types = sorted({t for n in ld for t in _types(n)})
    viewport = bool(_meta_value(metas, "viewport"))

    issues: list[dict] = []
    if status >= 400:
        issues.append(_issue("critical", f"La page répond {status}", "Corriger ou rediriger (301) cette URL"))
    if not title:
        issues.append(_issue("critical", "Balise <title> manquante", "Ajouter un title unique de 30 à 60 caractères"))
    elif len(title) < 30 or len(title) > 65:
        issues.append(_issue("important", f"Title de {len(title)} caractères (idéal 30–60)", "Réécrire le title avec le mot-clé principal en tête"))
    if not desc:
        issues.append(_issue("important", "Meta description manquante", "Ajouter une meta description de 120 à 155 caractères orientée bénéfice"))
    elif len(desc) < 70 or len(desc) > 165:
        issues.append(_issue("minor", f"Meta description de {len(desc)} caractères (idéal 120–155)", "Ajuster la longueur pour éviter la troncature"))
    if len(h1) == 0:
        issues.append(_issue("critical", "Aucun H1", "Ajouter un H1 unique et descriptif"))
    elif len(h1) > 1:
        issues.append(_issue("important", f"{len(h1)} balises H1", "Garder un seul H1, passer les autres en H2"))
    elif len(h1[0]) < 20:
        issues.append(_issue("minor", f"H1 court ({len(h1[0])} car.) : « {h1[0]} »", "Rendre le H1 plus descriptif (> 20 caractères)"))
    if h1:
        h1_terms = [w for w in _tokens(h1[0]) if w not in _STOP and len(w) >= 4]
        if h1_terms:
            body_counts = Counter(toks)
            used = [w for w in h1_terms if body_counts.get(w, 0) >= 2]
            if len(used) < max(1, len(h1_terms) // 2):
                issues.append(_issue("minor", "Les mots du H1 sont peu repris dans le contenu",
                                     "Réutiliser les termes du H1 dans l'intro et les sous-titres",
                                     h1_terms_missing=[w for w in h1_terms if w not in used][:8]))
    if missing_alt:
        issues.append(_issue("important", f"{len(missing_alt)} image(s) sans attribut alt", "Ajouter un alt descriptif (ou alt=\"\" si décorative)"))
    if not canonical:
        issues.append(_issue("important", "Balise canonical absente", f'Ajouter <link rel="canonical" href="{url}">'))
    elif _strip_www(_host(canonical)) != _strip_www(host):
        issues.append(_issue("important", f"Canonical vers un autre domaine : {canonical}"))
    if "noindex" in robots_meta:
        issues.append(_issue("critical", "Page en noindex (exclue de Google)", "Retirer noindex si la page doit être indexée"))
    if not viewport:
        issues.append(_issue("important", "Meta viewport absente (mobile)", 'Ajouter <meta name="viewport" content="width=device-width, initial-scale=1">'))
    if not lang:
        issues.append(_issue("minor", "Attribut lang absent sur <html>", 'Ajouter <html lang="fr">'))
    if words < 250 and status < 400:
        issues.append(_issue("important", f"Contenu mince ({words} mots)", "Viser 300+ mots utiles sur les pages à positionner"))
    if len(heads) > 15 and words / max(1, len(heads)) < 45:
        issues.append(_issue("minor", f"{len(heads)} titres pour {words} mots", "Moins de titres ou plus de texte par section"))
    if not _meta_value(metas, "og:title") or not _meta_value(metas, "og:image"):
        issues.append(_issue("minor", "Open Graph incomplet (og:title / og:image)", "Ajouter og:title, og:description, og:image (1200×630)"))
    if not ld:
        issues.append(_issue("minor", "Aucune donnée structurée JSON-LD", "Utiliser schema_generate (Organization, WebSite, FAQPage…)"))

    return {
        "url": url,
        "status": status,
        "title": title,
        "title_length": len(title),
        "description": desc,
        "description_length": len(desc),
        "h1": h1,
        "h2": h2[:20],
        "headings_count": len(heads),
        "words": words,
        "canonical": canonical,
        "robots_meta": robots_meta,
        "lang": lang,
        "viewport": viewport,
        "hreflang": hreflang,
        "images": len(imgs),
        "images_missing_alt": len(missing_alt),
        "internal_links": len(internal),
        "external_links": len(external),
        "json_ld_types": ld_types,
        "og": {k: _meta_value(metas, k) for k in ("og:title", "og:description", "og:image")},
        "issues": _sort_issues(issues),
        "score": _score_from(issues),
        "_internal_urls": sorted({a["url"] for a in internal}),
        "_anchors": anchors,
        "_text": text,
        "_raw": raw,
        "_ld": ld,
        "_heads": heads,
    }


def _public(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}


async def _crawl(start: str, ctx: dict, max_pages: int, max_depth: int = 3,
                 robots_groups: list[dict] | None = None) -> list[dict]:
    host = _host(start)
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(start, 0)]
    pages: list[dict] = []
    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        url = url.split("#")[0]
        if url in seen:
            continue
        seen.add(url)
        path = urlparse(url).path or "/"
        if path.lower().endswith(SKIP_EXT):
            continue
        if robots_groups is not None and pages and not _robots_allowed(robots_groups, UA, path):
            continue
        try:
            r = await _get(url, ctx)
        except PermissionError as e:
            pages.append({"url": url, "status": 0, "error": f"refusé : {e}", "issues": [], "score": 0})
            continue
        except httpx.HTTPError as e:
            pages.append({"url": url, "status": 0, "error": str(e)[:160],
                          "issues": [_issue("critical", "Page injoignable")], "score": 0})
            continue
        final = str(r.url).split("#")[0]
        if not pages:
            host = _host(final) or host
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype:
            if r.status_code >= 400:
                pages.append({"url": final, "status": r.status_code, "score": 0,
                              "issues": [_issue("critical", f"La page répond {r.status_code}",
                                                "Corriger ou rediriger (301) cette URL")]})
            continue
        rep = _page_report(r.text, final, r.status_code, r.headers)
        if final != url:
            rep["redirected_from"] = url
        pages.append(rep)
        seen.add(final)
        if depth < max_depth:
            for link in rep["_internal_urls"]:
                lp = urlparse(link).path or "/"
                if link not in seen and not lp.lower().endswith(SKIP_EXT) and _same_site(link, host) \
                        and all(link != q[0] for q in queue):
                    queue.append((link, depth + 1))
    return pages


# --- Sauvegarde -----------------------------------------------------------------------

def _save(ctx: dict, name: str, data: Any, markdown: str | None = None, ext: str = "json") -> dict:
    T = _t()
    base = re.sub(r"[^\w.\-]+", "_", str(name or "seo-rapport")).strip("._") or "seo-rapport"
    base = re.sub(r"\.(json|md|txt)$", "", base, flags=re.I)
    out: dict = {}
    ws = T._user_workspace(ctx)

    def write(rel: str, content: str) -> dict:
        p = T._safe_path(rel, ctx)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        r = str(p.relative_to(ws)).replace("\\", "/")
        return {"path": r, "download_url": f"/api/workspace/download?path={quote(r)}"}

    if ext == "txt":
        out["file"] = write(f"seo/{base}.txt", data if isinstance(data, str) else str(data))
    else:
        out["json"] = write(f"seo/{base}.json", json.dumps(data, ensure_ascii=False, indent=2))
    if markdown:
        out["markdown"] = write(f"seo/{base}.md", markdown)
    return out


def _md_issues(issues: list[dict], limit: int = 40) -> str:
    lines = []
    for i in issues[:limit]:
        where = f" — {i['count']} page(s)" if i.get("count") else ""
        fix = f"\n  → {i['fix']}" if i.get("fix") else ""
        lines.append(f"- **{i['priority']}** {i['message']}{where}{fix}")
    return "\n".join(lines) or "- Aucun problème détecté."


def _compact(data: Any) -> str:
    return _t()._truncate(json.dumps(data, ensure_ascii=False, indent=1))


# =====================================================================================
# 1) Audit SEO technique du site
# =====================================================================================

async def tool_seo_site_audit(args: dict, ctx: dict) -> str:
    url = _norm_url(args.get("url"))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    max_pages = max(1, min(30, int(args.get("max_pages") or 10)))
    origin = _origin(url)
    host = _host(url)

    site_issues: list[dict] = []
    checks: dict[str, Any] = {}

    # HTTPS + redirection http -> https
    if url.startswith("http://"):
        site_issues.append(_issue("critical", "Le site est analysé en http://", "Servir le site en HTTPS"))
    pr = await _probe(f"http://{host}/", ctx)
    checks["http_to_https"] = pr
    if pr.get("status") in (301, 308) and pr.get("location", "").startswith("https://"):
        pass
    elif pr.get("status") in (302, 307):
        site_issues.append(_issue("important", "http → https en redirection temporaire (302/307)", "Utiliser une 301"))
    elif pr.get("status") == 200:
        site_issues.append(_issue("critical", "La version http:// répond 200 sans rediriger vers https", "Rediriger tout http vers https en 301"))

    # Variante www / non-www
    alt = _strip_www(host) if host.startswith("www.") else "www." + host
    pa = await _probe(f"https://{alt}/", ctx)
    checks["www_variant"] = {"host": alt, **pa}
    loc_host = _host(urljoin(f"https://{alt}/", pa.get("location", ""))) if pa.get("location") else ""
    if pa.get("error") == "dns":
        site_issues.append(_issue("minor", f"La variante {alt} n'existe pas en DNS (pas de duplication)",
                                  f"Optionnel : créer le DNS {alt} et le rediriger en 301 vers {host} (certains outils SEO le signalent)"))
    elif pa.get("status") in (301, 308) and loc_host == host:
        pass
    elif pa.get("status") in (302, 307):
        site_issues.append(_issue("important", f"{alt} redirige en temporaire (302/307)", "Passer en 301"))
    elif pa.get("status") == 200:
        site_issues.append(_issue("critical", f"{host} et {alt} servent tous les deux le contenu (duplicate)",
                                  f"Rediriger {alt} vers {host} en 301 (ex. Coolify : Direction « Redirect to non-www »)"))
    elif pa.get("error") == "cert":
        site_issues.append(_issue("important", f"{alt} : certificat HTTPS invalide", f"Émettre un certificat pour {alt} puis rediriger en 301"))
    elif (pa.get("status") or 0) >= 400:
        site_issues.append(_issue("important", f"{alt} répond {pa.get('status')}", f"Déclarer {alt} sur le serveur et le rediriger en 301 vers {host}"))

    # robots.txt
    robots = await _fetch_robots(origin, ctx)
    checks["robots_txt"] = {k: robots.get(k) for k in ("status", "present", "sitemaps")}
    if not robots["present"]:
        site_issues.append(_issue("important", "robots.txt absent", "Créer /robots.txt avec une ligne Sitemap:"))
    elif not _robots_allowed(robots["groups"], "Googlebot", urlparse(url).path or "/"):
        site_issues.append(_issue("critical", "robots.txt bloque Googlebot sur cette page", "Retirer le Disallow correspondant"))

    # sitemap
    sm_urls = robots.get("sitemaps") or [origin + "/sitemap.xml"]
    sm_info: dict[str, Any] = {"url": sm_urls[0]}
    try:
        rs = await _get(sm_urls[0], ctx, timeout=15)
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rs.text, re.I)
        sm_info.update({"status": rs.status_code, "urls": len(locs),
                        "index": bool(re.search(r"<sitemapindex", rs.text, re.I))})
        if rs.status_code != 200 or not locs:
            site_issues.append(_issue("important", "Sitemap XML absent ou vide", "Publier /sitemap.xml et le référencer dans robots.txt"))
        else:
            foreign = [l for l in locs if not _same_site(l, host)]
            if foreign:
                site_issues.append(_issue("minor", f"{len(foreign)} URL(s) du sitemap hors domaine"))
            if not robots.get("sitemaps"):
                site_issues.append(_issue("minor", "Sitemap non déclaré dans robots.txt", f"Ajouter « Sitemap: {sm_urls[0]} »"))
    except (httpx.HTTPError, PermissionError) as e:
        sm_info["error"] = str(e)[:120]
        site_issues.append(_issue("important", "Sitemap XML injoignable"))
    checks["sitemap"] = sm_info

    # Gestion des 404
    try:
        r404 = await _get(origin + "/maltai-seo-check-404-xyz", ctx, timeout=15)
        checks["not_found_status"] = r404.status_code
        if r404.status_code == 200:
            site_issues.append(_issue("important", "Soft 404 : une URL inexistante répond 200", "Renvoyer un vrai code 404"))
    except (httpx.HTTPError, PermissionError):
        pass

    # Crawl + audit page par page
    pages = await _crawl(url, ctx, max_pages, robots_groups=robots["groups"])
    html_pages = [p for p in pages if p.get("title") is not None and "_raw" in p]

    # Doublons inter-pages
    tcount = Counter(p["title"] for p in html_pages if p.get("title"))
    dcount = Counter(p["description"] for p in html_pages if p.get("description"))
    dup_titles = [t for t, c in tcount.items() if c > 1]
    dup_desc = [d for d, c in dcount.items() if c > 1]
    if dup_titles:
        site_issues.append(_issue("important", f"{len(dup_titles)} title(s) dupliqué(s) entre pages", "Un title unique par page", examples=dup_titles[:3]))
    if dup_desc:
        site_issues.append(_issue("minor", f"{len(dup_desc)} meta description(s) dupliquée(s)", "Une description unique par page"))
    broken = [p["url"] for p in pages if (p.get("status") or 0) >= 400]
    if broken:
        site_issues.append(_issue("critical", f"{len(broken)} page(s) interne(s) en erreur", "Corriger les liens ou rediriger en 301", urls=broken[:10]))

    # Agregation des problemes de pages
    agg: dict[tuple[str, str], dict] = {}
    for p in pages:
        for i in p.get("issues", []):
            key = (i["severity"], re.sub(r"\d+", "#", i["message"]))
            if key not in agg:
                agg[key] = {**i, "count": 0, "pages": []}
            agg[key]["count"] += 1
            if len(agg[key]["pages"]) < 5:
                agg[key]["pages"].append(p["url"])
    page_issues = _sort_issues(list(agg.values()))

    site_score = _score_from(site_issues)
    avg_page = round(sum(p.get("score", 0) for p in pages) / len(pages)) if pages else 0
    score = round(site_score * 0.4 + avg_page * 0.6)
    all_issues = _sort_issues(site_issues) + page_issues

    report = {
        "url": url,
        "score": score,
        "grade": _grade(score),
        "site_score": site_score,
        "pages_score": avg_page,
        "pages_crawled": len(pages),
        "checks": checks,
        "site_issues": _sort_issues(site_issues),
        "page_issues": page_issues,
        "pages": [_public(p) for p in pages],
    }
    md = [f"# Audit SEO — {url}", "",
          f"**Score : {score}/100 ({_grade(score)})** · site {site_score} · pages {avg_page} · {len(pages)} page(s) analysée(s)", "",
          "## Configuration serveur", "",
          f"- http → https : {checks['http_to_https'].get('status') or checks['http_to_https'].get('error')}",
          f"- Variante {alt} : {pa.get('status') or pa.get('error')} {pa.get('location', '')}",
          f"- robots.txt : {'présent' if robots['present'] else 'absent'}",
          f"- sitemap : {sm_info.get('urls', 0)} URL(s) ({sm_info.get('url')})", "",
          "## Problèmes prioritaires", "", _md_issues(all_issues), "",
          "## Pages", "", "| URL | Statut | Score | Title | Desc | H1 | Mots |", "|---|---|---|---|---|---|---|"]
    for p in pages:
        md.append(f"| {p['url']} | {p.get('status')} | {p.get('score')} | {p.get('title_length', '-')} | "
                  f"{p.get('description_length', '-')} | {len(p.get('h1', []))} | {p.get('words', '-')} |")
    exports = _save(ctx, args.get("save_as") or f"audit-seo-{host}", report, "\n".join(md))

    summary = {
        "url": url, "score": score, "grade": _grade(score),
        "site_score": site_score, "pages_score": avg_page, "pages_crawled": len(pages),
        "checks": {
            "http_to_https": checks["http_to_https"].get("status") or checks["http_to_https"].get("error"),
            "www_variant": {"host": alt, "status": pa.get("status"), "error": pa.get("error"), "location": pa.get("location")},
            "robots_txt": robots["present"], "sitemap_urls": sm_info.get("urls", 0),
            "not_found_status": checks.get("not_found_status"),
        },
        "top_issues": [{k: v for k, v in i.items() if k in ("priority", "message", "fix", "count")} for i in all_issues[:12]],
        # les 15 pages les plus faibles d'abord ; la liste complete est dans le rapport exporte
        "worst_pages": [{"url": p["url"], "status": p.get("status"), "score": p.get("score"),
                         "issues": len(p.get("issues", []))}
                        for p in sorted(pages, key=lambda x: x.get("score", 0))[:15]],
        "exports": exports,
    }
    return _compact(summary)


# =====================================================================================
# 2) SEO local
# =====================================================================================

LOCAL_TYPES = {
    "LocalBusiness", "Restaurant", "Store", "ProfessionalService", "MedicalBusiness", "LegalService",
    "HomeAndConstructionBusiness", "AutomotiveBusiness", "FoodEstablishment", "LodgingBusiness",
    "HealthAndBeautyBusiness", "SportsActivityLocation", "EntertainmentBusiness", "FinancialService",
    "RealEstateAgent", "TravelAgency", "Dentist", "Physician", "Hotel", "Bakery", "CafeOrCoffeeShop",
    "BarOrPub", "HairSalon", "BeautySalon", "Electrician", "Plumber", "Locksmith", "RoofingContractor",
    "GeneralContractor", "AutoRepair", "Attorney", "Notary", "AccountingService", "InsuranceAgency",
    "ChildCare", "Pharmacy", "Optician", "VeterinaryCare", "DaySpa", "Florist", "GroceryStore",
    "HardwareStore", "ClothingStore", "BookStore", "PetStore", "Winery", "Brewery", "Campground",
}
_PHONE_RE = re.compile(r"(?:\+33\s?\(?0?\)?\s?|\b0)[1-9](?:[\s.\-]?\d{2}){4}\b|\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}")
_POSTAL_RE = re.compile(r"\b(\d{5})\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ'’\- ]{2,40})")
_STREET_RE = re.compile(
    r"\b\d{1,4}(?:\s?(?:bis|ter))?,?\s+(?:rue|avenue|av\.|boulevard|bd|place|chemin|route|allée|allee|impasse|quai|cours|square|lieu-dit)\s+[^,<\n]{2,60}",
    re.I)
_DAYS_RE = re.compile(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_HOURS_RE = re.compile(r"\b\d{1,2}\s?(?:h|:)\s?\d{0,2}\b")
_MAP_RE = re.compile(r"google\.[a-z.]+/maps|maps\.google|maps\.app\.goo\.gl|goo\.gl/maps|openstreetmap\.org|waze\.com|bing\.com/maps|apple\.com/maps|maps\.apple", re.I)


def _digits(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    if d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    return d


def _is_local(node: dict) -> bool:
    for t in _types(node):
        if t in LOCAL_TYPES or t.endswith(("Business", "Store", "Shop", "Restaurant", "Agency", "Clinic", "Salon")):
            return True
    return False


async def tool_local_seo_audit(args: dict, ctx: dict) -> str:
    url = _norm_url(args.get("url"))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    want_name = str(args.get("business_name") or "").strip()
    want_phone = _digits(str(args.get("phone") or ""))
    want_city = str(args.get("city") or "").strip()
    want_address = str(args.get("address") or "").strip()
    max_pages = max(1, min(8, int(args.get("max_pages") or 4)))

    try:
        r = await _get(url, ctx)
    except PermissionError as e:
        return f"Refuse : {e}"
    except httpx.HTTPError as e:
        return f"Erreur : {e}"
    home = _page_report(r.text, str(r.url), r.status_code)
    pages = [home]
    # pages utiles au local : contact, acces, a propos, mentions
    candidates = []
    for a in home["_anchors"]:
        blob = (a["url"] + " " + a["text"]).lower()
        if a["url"].startswith("http") and _same_site(a["url"], _host(home["url"])) and re.search(
                r"contact|nous-trouver|acces|accès|plan|a-propos|about|mentions|magasin|agence|boutique|horaires", blob):
            if a["url"] not in candidates and a["url"] != home["url"]:
                candidates.append(a["url"])
    for cu in candidates[: max_pages - 1]:
        try:
            rc = await _get(cu, ctx)
            if "html" in rc.headers.get("content-type", ""):
                pages.append(_page_report(rc.text, str(rc.url), rc.status_code))
        except (httpx.HTTPError, PermissionError):
            continue

    phones, postals, streets, maps, tel_links = set(), set(), set(), set(), set()
    days_found, hours_found = False, False
    local_nodes, org_nodes = [], []
    for p in pages:
        txt = p["_text"]
        for m in _PHONE_RE.findall(txt):
            d = _digits(m)
            if 9 <= len(d) <= 13:
                phones.add(d)
        for cp, city in _POSTAL_RE.findall(txt):
            postals.add(f"{cp} {city.strip()[:40]}")
        for st in _STREET_RE.findall(txt):
            streets.add(st.strip()[:80])
        if _DAYS_RE.search(txt) and _HOURS_RE.search(txt):
            days_found = hours_found = True
        for a in p["_anchors"]:
            if a["href"].startswith("tel:"):
                tel_links.add(_digits(a["href"]))
            if _MAP_RE.search(a["href"]):
                maps.add(a["href"][:160])
        for src in re.findall(r"<iframe\b[^>]*src=[\"']([^\"']+)", p["_raw"], re.I):
            if _MAP_RE.search(src):
                maps.add(src[:160])
        for n in p["_ld"]:
            if _is_local(n):
                local_nodes.append(n)
            elif "Organization" in _types(n):
                org_nodes.append(n)

    node = local_nodes[0] if local_nodes else {}
    addr = node.get("address") if isinstance(node.get("address"), dict) else {}
    fields = {
        "name": bool(node.get("name")),
        "address.streetAddress": bool(addr.get("streetAddress")),
        "address.postalCode": bool(addr.get("postalCode")),
        "address.addressLocality": bool(addr.get("addressLocality")),
        "address.addressCountry": bool(addr.get("addressCountry")),
        "telephone": bool(node.get("telephone")),
        "openingHours": bool(node.get("openingHoursSpecification") or node.get("openingHours")),
        "geo": bool(node.get("geo")),
        "url": bool(node.get("url")),
        "image": bool(node.get("image") or node.get("logo")),
        "priceRange": bool(node.get("priceRange")),
        "sameAs": bool(node.get("sameAs")),
        "aggregateRating": bool(node.get("aggregateRating")),
        "areaServed": bool(node.get("areaServed")),
    }

    issues: list[dict] = []
    if not local_nodes:
        issues.append(_issue("critical", "Aucun schéma LocalBusiness (ou sous-type) détecté",
                             "Générer un JSON-LD LocalBusiness avec schema_generate et l'ajouter dans le <head>"))
    else:
        for f in ("name", "address.streetAddress", "address.postalCode", "address.addressLocality", "telephone"):
            if not fields[f]:
                issues.append(_issue("important", f"Champ LocalBusiness manquant : {f}"))
        for f in ("openingHours", "geo", "image", "url"):
            if not fields[f]:
                issues.append(_issue("minor", f"Champ LocalBusiness recommandé absent : {f}"))
        if not fields["sameAs"]:
            issues.append(_issue("minor", "Pas de sameAs (fiche Google, Facebook, Pages Jaunes…)", "Lier les profils officiels via sameAs"))
    if not phones and not tel_links:
        issues.append(_issue("critical", "Aucun numéro de téléphone visible", "Afficher le téléphone en texte + lien tel:"))
    elif not tel_links:
        issues.append(_issue("important", "Téléphone sans lien cliquable tel:", '<a href="tel:+33...">'))
    if len(phones) > 1:
        issues.append(_issue("important", f"{len(phones)} numéros différents trouvés (cohérence NAP)", "Garder un numéro principal identique partout", phones=sorted(phones)[:5]))
    if not postals and not streets:
        issues.append(_issue("critical", "Adresse postale introuvable dans le texte", "Afficher l'adresse complète (rue, code postal, ville) en texte"))
    if not maps:
        issues.append(_issue("important", "Pas de carte ni de lien itinéraire", "Intégrer Google Maps / lien itinéraire"))
    if not (days_found and hours_found) and not fields["openingHours"]:
        issues.append(_issue("important", "Horaires d'ouverture introuvables", "Afficher les horaires + openingHoursSpecification"))
    if not fields["aggregateRating"] and not re.search(r"\bavis\b|\breviews?\b|étoiles|★", home["_text"], re.I):
        issues.append(_issue("minor", "Aucun avis client visible", "Afficher des avis réels et répondre aux avis Google"))

    # Coherence avec les donnees fournies
    consistency = {}
    if want_name:
        nm = want_name.lower()
        consistency["name_in_title"] = nm in home["title"].lower()
        consistency["name_in_text"] = nm in home["_text"].lower()
        consistency["name_in_schema"] = nm in str(node.get("name", "")).lower()
        if not consistency["name_in_schema"] and local_nodes:
            issues.append(_issue("important", "Le nom du schéma ne correspond pas au nom fourni"))
    if want_phone:
        consistency["phone_on_site"] = want_phone in phones or want_phone in tel_links
        consistency["phone_in_schema"] = want_phone == _digits(str(node.get("telephone", "")))
        if not consistency["phone_on_site"]:
            issues.append(_issue("critical", "Le téléphone fourni n'apparaît pas sur le site (NAP incohérent)"))
    if want_city:
        c = want_city.lower()
        consistency["city_in_title"] = c in home["title"].lower()
        consistency["city_in_h1"] = any(c in h.lower() for h in home["h1"])
        consistency["city_in_text"] = c in home["_text"].lower()
        if not consistency["city_in_title"] and not consistency["city_in_h1"]:
            issues.append(_issue("important", f"La ville « {want_city} » n'est ni dans le title ni dans le H1",
                                 f"Ex. title : « {want_name or 'Activité'} à {want_city} — … »"))
    if want_address:
        consistency["address_in_text"] = want_address.lower()[:20] in home["_text"].lower()

    # Brouillon JSON-LD pre-rempli
    draft_data = {
        "name": want_name or node.get("name") or (home["title"].split("—")[0].split("|")[0].strip()),
        "url": _origin(home["url"]) + "/",
        "telephone": (str(args.get("phone") or "") or node.get("telephone") or (sorted(phones)[0] if phones else "")),
        "street": want_address or addr.get("streetAddress") or (sorted(streets)[0] if streets else ""),
        "city": want_city or addr.get("addressLocality") or "",
        "postal_code": addr.get("postalCode") or (sorted(postals)[0].split()[0] if postals else ""),
        "country": addr.get("addressCountry") or "FR",
        "business_type": args.get("business_type") or (_types(node)[0] if node else "LocalBusiness"),
        "image": home["og"].get("og:image", ""),
        "description": home["description"],
    }
    draft = _build_schema("localbusiness", draft_data)

    score = _score_from(issues)
    report = {
        "url": home["url"], "score": score, "grade": _grade(score),
        "pages_checked": [p["url"] for p in pages],
        "nap_found": {"phones": sorted(phones)[:6], "tel_links": sorted(tel_links)[:6],
                      "postal_city": sorted(postals)[:6], "streets": sorted(streets)[:6]},
        "maps": sorted(maps)[:5], "opening_hours_in_text": days_found and hours_found,
        "local_schema_types": sorted({t for n in local_nodes for t in _types(n)}),
        "local_schema_fields": fields, "consistency": consistency,
        "issues": _sort_issues(issues),
        "jsonld_draft": draft,
        "google_business_profile_checklist": [
            "Revendiquer/valider la fiche Google Business Profile",
            "Nom, adresse, téléphone strictement identiques au site (NAP)",
            "Catégorie principale précise + catégories secondaires",
            "Horaires (y compris jours fériés) et zone desservie",
            "10+ photos réelles (façade, intérieur, équipe, produits)",
            "Lien vers le site (avec UTM) et prise de RDV si applicable",
            "Demander des avis réguliers et répondre à chacun",
            "Publier des posts Google (offres, actus) chaque mois",
            "Citations cohérentes : Pages Jaunes, Apple Plans, Bing Places, annuaires locaux",
        ],
    }
    md = (f"# Audit SEO local — {home['url']}\n\n**Score : {score}/100 ({_grade(score)})**\n\n"
          f"## Problèmes\n\n{_md_issues(report['issues'])}\n\n## Brouillon JSON-LD\n\n```html\n"
          f"<script type=\"application/ld+json\">\n{json.dumps(draft, ensure_ascii=False, indent=2)}\n</script>\n```\n\n"
          "## Checklist Google Business Profile\n\n" + "\n".join(f"- [ ] {c}" for c in report["google_business_profile_checklist"]))
    exports = _save(ctx, args.get("save_as") or f"audit-local-{_host(home['url'])}", report, md)
    out = {k: v for k, v in report.items() if k != "google_business_profile_checklist"}
    out["issues"] = [{k: v for k, v in i.items() if k in ("priority", "message", "fix")} for i in report["issues"]]
    out["exports"] = exports
    return _compact(out)


# =====================================================================================
# 3) GEO — Generative Engine Optimization
# =====================================================================================

AI_BOTS = [
    # (user-agent, editeur, role, est_un_bot_de_recherche_ou_utilisateur)
    ("OAI-SearchBot", "OpenAI", "recherche ChatGPT", True),
    ("ChatGPT-User", "OpenAI", "navigation à la demande", True),
    ("GPTBot", "OpenAI", "entraînement des modèles", False),
    ("Claude-SearchBot", "Anthropic", "recherche Claude", True),
    ("Claude-User", "Anthropic", "navigation à la demande", True),
    ("ClaudeBot", "Anthropic", "entraînement des modèles", False),
    ("PerplexityBot", "Perplexity", "index de recherche", True),
    ("Perplexity-User", "Perplexity", "navigation à la demande", True),
    ("Googlebot", "Google", "recherche + AI Overviews", True),
    ("Google-Extended", "Google", "Gemini (entraînement)", False),
    ("Bingbot", "Microsoft", "Bing + Copilot", True),
    ("Applebot-Extended", "Apple", "Apple Intelligence (entraînement)", False),
    ("DuckAssistBot", "DuckDuckGo", "DuckAssist", True),
    ("MistralAI-User", "Mistral", "Le Chat (navigation)", True),
    ("CCBot", "Common Crawl", "jeux de données d'entraînement", False),
    ("Meta-ExternalAgent", "Meta", "Meta AI (entraînement)", False),
    ("Amazonbot", "Amazon", "Alexa / IA", False),
    ("Bytespider", "ByteDance", "entraînement", False),
]
_QUESTION_RE = re.compile(
    r"\?\s*$|^(comment|pourquoi|quoi|quel|quelle|quels|quelles|qu['’]est|combien|est-ce|peut-on|faut-il|où|quand|"
    r"how|what|why|which|when|where|can|is|are|does|do)\b", re.I)
_STAT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|€|\$|k€|m€|ms|go|mo|ko|kg|km|ans|jours|heures|mois|x)\b", re.I)
_TRUST_RE = re.compile(r"a-propos|about|contact|mentions|legal|privacy|confidentialit|equipe|team|cgu|cgv", re.I)


async def tool_geo_audit(args: dict, ctx: dict) -> str:
    url = _norm_url(args.get("url"))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    try:
        r = await _get(url, ctx)
    except PermissionError as e:
        return f"Refuse : {e}"
    except httpx.HTTPError as e:
        return f"Erreur : {e}"
    page = _page_report(r.text, str(r.url), r.status_code)
    origin = _origin(page["url"])
    path = urlparse(page["url"]).path or "/"
    metas = _metas(page["_raw"])
    brand = str(args.get("brand") or _meta_value(metas, "og:site_name") or
                re.split(r"\s[—|\-–:]\s", page["title"] or "")[0]).strip()

    # 1. Acces des crawlers IA (25)
    robots = await _fetch_robots(origin, ctx)
    bots = []
    blocked_search, blocked_training = [], []
    for ua, vendor, role, is_search in AI_BOTS:
        allowed = _robots_allowed(robots["groups"], ua, path)
        bots.append({"bot": ua, "vendor": vendor, "role": role, "allowed": allowed})
        if not allowed:
            (blocked_search if is_search else blocked_training).append(ua)
    s_access = max(0, 25 - 6 * len(blocked_search))

    # 2. llms.txt (10)
    llms = {"present": False}
    try:
        rl = await _get(origin + "/llms.txt", ctx, timeout=15)
        body = rl.text.strip()
        ok = rl.status_code == 200 and body.startswith("#") and "<html" not in body[:300].lower()
        llms = {"present": ok, "status": rl.status_code, "size": len(body) if ok else 0,
                "sections": len(re.findall(r"^##\s", body, re.M)) if ok else 0}
    except (httpx.HTTPError, PermissionError) as e:
        llms = {"present": False, "error": str(e)[:120]}
    s_llms = 10 if llms.get("present") else 0

    # 3. Donnees structurees (20)
    ld = page["_ld"]
    types = set(page["json_ld_types"])
    org = next((n for n in ld if {"Organization", "Corporation", "LocalBusiness"} & set(_types(n)) or _is_local(n)), None)
    same_as = []
    for n in ld:
        sa = n.get("sameAs")
        if isinstance(sa, str):
            same_as.append(sa)
        elif isinstance(sa, list):
            same_as += [str(x) for x in sa]
    same_as = list(dict.fromkeys(same_as))
    main_entity = types & {"Article", "BlogPosting", "NewsArticle", "Product", "SoftwareApplication", "Service",
                           "Course", "Recipe", "Event", "HowTo"} or any(_is_local(n) for n in ld)
    s_schema = (6 if ld else 0) + (5 if (org or "WebSite" in types) and same_as else (2 if org or "WebSite" in types else 0)) \
        + (4 if "FAQPage" in types else 0) + (5 if main_entity else 0)

    # 4. Contenu citable (25)
    heads = page["_heads"]
    q_heads = [t for lv, t in heads if lv in (2, 3, 4) and _QUESTION_RE.search(t.strip())]
    raw = page["_raw"]
    faq = "FAQPage" in types or bool(re.search(r"<details\b", raw, re.I)) or any(
        re.search(r"faq|questions fréquentes|questions frequentes", t, re.I) for _, t in heads)
    lists = len(re.findall(r"<(ul|ol)\b", raw, re.I))
    tables = len(re.findall(r"<table\b", raw, re.I))
    stats = len(_STAT_RE.findall(page["_text"]))
    first_p = ""
    for ptxt in re.findall(r"<p\b[^>]*>(.*?)</p>", raw, re.I | re.S):
        t = _clean_text(ptxt)
        if len(t) > 60:
            first_p = t
            break
    definition = bool(re.search(r"\b(est|sont|permet|aide|is|are|helps)\b", first_p[:300], re.I))
    words = page["words"]
    s_content = (5 if words >= 600 else 3 if words >= 300 else 0) + (4 if len(q_heads) >= 2 else 2 if q_heads else 0) \
        + (4 if faq else 0) + (3 if lists >= 2 else 1 if lists else 0) + (2 if tables else 0) \
        + (3 if stats >= 3 else 1 if stats else 0) + (2 if definition else 0) + (2 if page["description"] else 0)

    # 5. Entite & confiance (20)
    bl = brand.lower()
    brand_visible = bool(bl) and (bl in page["title"].lower() or any(bl in h.lower() for h in page["h1"]))
    author = bool(_meta_value(metas, "author")) or any(n.get("author") for n in ld) or bool(re.search(r'rel=["\']author', raw, re.I))
    dated = any(n.get("dateModified") or n.get("datePublished") for n in ld) or bool(
        re.search(r"<time\b[^>]*datetime=", raw, re.I)) or bool(_meta_value(metas, "article:modified_time")) \
        or bool(r.headers.get("last-modified"))
    trust = sorted({a["url"] for a in page["_anchors"] if a["url"].startswith("http") and _TRUST_RE.search(a["url"] + " " + a["text"])})
    ext_cites = page["external_links"]
    s_entity = (4 if brand_visible else 0) + (3 if author else 0) + (4 if dated else 0) \
        + (4 if len(trust) >= 2 else 2 if trust else 0) + (3 if len(same_as) >= 2 else 1 if same_as else 0) \
        + (2 if ext_cites >= 2 else 0)

    score = min(100, s_access + s_llms + s_schema + s_content + s_entity)

    recs: list[dict] = []
    if blocked_search:
        recs.append(_issue("critical", f"robots.txt bloque des bots de recherche IA : {', '.join(blocked_search)}",
                           "Autoriser ces bots (User-agent: <bot> / Allow: /) pour être cité dans les réponses IA"))
    if blocked_training:
        recs.append(_issue("minor", f"Bots d'entraînement bloqués : {', '.join(blocked_training)}",
                           "Choix éditorial : cela n'empêche pas d'être cité via les bots de recherche"))
    if not llms.get("present"):
        recs.append(_issue("important", "Pas de /llms.txt", "Générer le fichier avec llms_txt_generate et le publier à la racine"))
    if not ld:
        recs.append(_issue("critical", "Aucune donnée structurée", "Ajouter Organization + WebSite + le type principal (schema_generate)"))
    else:
        if not org:
            recs.append(_issue("important", "Pas d'entité Organization / LocalBusiness", "Déclarer l'organisation (nom, logo, url, sameAs)"))
        if not same_as:
            recs.append(_issue("important", "Pas de sameAs", "Lier LinkedIn, GitHub, Wikipedia/Wikidata, réseaux officiels"))
        if "FAQPage" not in types:
            recs.append(_issue("important", "Pas de FAQPage", "Ajouter une FAQ visible + son balisage FAQPage"))
        if not main_entity:
            recs.append(_issue("minor", "Type principal non déclaré (Product, Service, SoftwareApplication, Article…)"))
    if words < 600:
        recs.append(_issue("important", f"Contenu court pour une source IA ({words} mots)", "Développer : définitions, cas d'usage, chiffres, comparatifs"))
    if len(q_heads) < 2:
        recs.append(_issue("important", "Peu de sous-titres formulés en questions", "Reprendre les questions réelles des utilisateurs en H2/H3"))
    if not faq:
        recs.append(_issue("important", "Pas de FAQ", "Ajouter 5–8 questions/réponses courtes et factuelles"))
    if stats < 3:
        recs.append(_issue("minor", "Peu de données chiffrées", "Ajouter chiffres, dates, résultats mesurables (les IA citent les faits)"))
    if not definition:
        recs.append(_issue("minor", "L'intro ne définit pas clairement l'offre", f"Commencer par « {brand or 'X'} est … qui permet … »"))
    if not tables and lists < 2:
        recs.append(_issue("minor", "Peu de listes / tableaux", "Structurer en listes et tableaux comparatifs (faciles à extraire)"))
    if not brand_visible:
        recs.append(_issue("important", "La marque n'apparaît ni dans le title ni dans le H1"))
    if not author:
        recs.append(_issue("minor", "Auteur / responsable éditorial non indiqué", "Ajouter l'auteur (meta author, schéma author)"))
    if not dated:
        recs.append(_issue("minor", "Pas de date de mise à jour détectable", "Ajouter dateModified (schéma) et une date visible"))
    if len(trust) < 2:
        recs.append(_issue("minor", "Peu de pages de confiance liées (à propos, contact, mentions)"))

    report = {
        "url": page["url"], "brand": brand, "geo_score": score, "grade": _grade(score),
        "breakdown": {"acces_crawlers_ia": f"{s_access}/25", "llms_txt": f"{s_llms}/10",
                      "donnees_structurees": f"{s_schema}/20", "contenu_citable": f"{s_content}/25",
                      "entite_confiance": f"{s_entity}/20"},
        "ai_bots": bots, "llms_txt": llms,
        "structured_data": {"types": sorted(types), "organization": bool(org), "same_as": same_as[:8]},
        "content": {"words": words, "question_headings": q_heads[:8], "faq": faq, "lists": lists,
                    "tables": tables, "stats": stats, "definition_intro": definition,
                    "first_paragraph": first_p[:220]},
        "entity": {"brand_in_title_or_h1": brand_visible, "author": author, "dated": dated,
                   "trust_pages": trust[:6], "external_links": ext_cites},
        "recommendations": _sort_issues(recs),
        "note": "Score heuristique : il mesure la capacité du site à être lu et cité par les moteurs IA, "
                "pas sa présence réelle dans leurs réponses. Pour tester la visibilité, poser des questions "
                "cibles aux assistants (ChatGPT, Claude, Perplexity, Gemini) et noter si la marque est citée.",
    }
    md = (f"# Audit GEO — {page['url']}\n\n**Score GEO : {score}/100 ({_grade(score)})** · marque : {brand}\n\n"
          + "\n".join(f"- {k} : {v}" for k, v in report["breakdown"].items())
          + "\n\n## Bots IA (robots.txt)\n\n| Bot | Éditeur | Rôle | Accès |\n|---|---|---|---|\n"
          + "\n".join(f"| {b['bot']} | {b['vendor']} | {b['role']} | {'✅' if b['allowed'] else '⛔'} |" for b in bots)
          + f"\n\n## Recommandations\n\n{_md_issues(report['recommendations'])}\n\n> {report['note']}\n")
    exports = _save(ctx, args.get("save_as") or f"audit-geo-{_host(page['url'])}", report, md)
    out = dict(report)
    out["ai_bots"] = {"blocked_search": blocked_search, "blocked_training": blocked_training,
                      "allowed": [b["bot"] for b in bots if b["allowed"]]}
    out["recommendations"] = [{k: v for k, v in i.items() if k in ("priority", "message", "fix")} for i in report["recommendations"]]
    out["exports"] = exports
    return _compact(out)


# =====================================================================================
# 4) Generateur de donnees structurees
# =====================================================================================

_DAY_MAP = {
    "lundi": "Monday", "mardi": "Tuesday", "mercredi": "Wednesday", "jeudi": "Thursday",
    "vendredi": "Friday", "samedi": "Saturday", "dimanche": "Sunday",
    "mo": "Monday", "tu": "Tuesday", "we": "Wednesday", "th": "Thursday", "fr": "Friday", "sa": "Saturday", "su": "Sunday",
}
SCHEMA_KINDS = ["localbusiness", "organization", "website", "faqpage", "softwareapplication",
                "article", "product", "breadcrumblist", "service"]


def _prune(x: Any) -> Any:
    if isinstance(x, dict):
        d = {k: _prune(v) for k, v in x.items()}
        d = {k: v for k, v in d.items() if v not in (None, "", [], {})}
        # un sous-objet ne contenant que @type est vide
        return {} if set(d) <= {"@type"} and "@context" not in d and len(d) <= 1 and d else d
    if isinstance(x, list):
        return [v for v in (_prune(i) for i in x) if v not in (None, "", [], {})]
    return x


def _as_list(v: Any) -> list:
    if v in (None, ""):
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str) and "," in v and v.startswith("http"):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [v]


def _day(d: Any) -> str:
    s = str(d).strip()
    return _DAY_MAP.get(s.lower(), _DAY_MAP.get(s.lower()[:2], s.capitalize()))


def _opening(spec: Any) -> list[dict]:
    out = []
    for item in _as_list(spec):
        if isinstance(item, dict):
            days = [_day(d) for d in _as_list(item.get("days") or item.get("dayOfWeek"))]
            out.append({"@type": "OpeningHoursSpecification", "dayOfWeek": days,
                        "opens": item.get("opens"), "closes": item.get("closes")})
    return out


def _address(d: dict) -> dict:
    return {"@type": "PostalAddress", "streetAddress": d.get("street") or d.get("streetAddress"),
            "postalCode": d.get("postal_code") or d.get("postalCode"),
            "addressLocality": d.get("city") or d.get("addressLocality"),
            "addressRegion": d.get("region") or d.get("addressRegion"),
            "addressCountry": d.get("country") or d.get("addressCountry")}


def _build_schema(kind: str, d: dict) -> dict:
    k = re.sub(r"[^a-z]", "", str(kind or "").lower())
    d = d or {}
    ctx = "https://schema.org"
    if k in ("localbusiness", "local", "business"):
        node = {
            "@context": ctx, "@type": d.get("business_type") or "LocalBusiness",
            "name": d.get("name"), "description": d.get("description"), "url": d.get("url"),
            "telephone": d.get("telephone") or d.get("phone"), "email": d.get("email"),
            "image": d.get("image"), "logo": d.get("logo"), "priceRange": d.get("price_range") or d.get("priceRange"),
            "address": _address(d),
            "geo": {"@type": "GeoCoordinates", "latitude": d.get("latitude"), "longitude": d.get("longitude")}
            if d.get("latitude") and d.get("longitude") else None,
            "openingHoursSpecification": _opening(d.get("opening_hours")),
            "openingHours": d.get("opening_hours") if isinstance(d.get("opening_hours"), str) else None,
            "areaServed": d.get("area_served") or d.get("areaServed"),
            "sameAs": _as_list(d.get("same_as") or d.get("sameAs")),
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": d.get("rating"), "reviewCount": d.get("review_count")}
            if d.get("rating") and d.get("review_count") else None,
        }
    elif k in ("organization", "org", "corporation"):
        node = {"@context": ctx, "@type": "Organization", "name": d.get("name"), "url": d.get("url"),
                "logo": d.get("logo"), "description": d.get("description"), "email": d.get("email"),
                "telephone": d.get("telephone") or d.get("phone"),
                "address": _address(d) if (d.get("city") or d.get("street")) else None,
                "sameAs": _as_list(d.get("same_as") or d.get("sameAs")),
                "contactPoint": {"@type": "ContactPoint", "contactType": d.get("contact_type") or "customer support",
                                 "email": d.get("email"), "telephone": d.get("telephone") or d.get("phone")}
                if (d.get("email") or d.get("telephone") or d.get("phone")) else None}
    elif k == "website":
        node = {"@context": ctx, "@type": "WebSite", "name": d.get("name"), "url": d.get("url"),
                "inLanguage": d.get("language") or "fr",
                "potentialAction": {"@type": "SearchAction", "target": d.get("search_url"),
                                    "query-input": "required name=search_term_string"} if d.get("search_url") else None}
    elif k in ("faqpage", "faq"):
        qa = d.get("faq") or d.get("questions") or []
        node = {"@context": ctx, "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q.get("question") or q.get("q"),
             "acceptedAnswer": {"@type": "Answer", "text": q.get("answer") or q.get("a")}}
            for q in qa if isinstance(q, dict)]}
    elif k in ("softwareapplication", "software", "app"):
        node = {"@context": ctx, "@type": "SoftwareApplication", "name": d.get("name"),
                "applicationCategory": d.get("category") or "BusinessApplication",
                "operatingSystem": d.get("operating_system") or "Web", "description": d.get("description"),
                "url": d.get("url"), "image": d.get("image"), "softwareVersion": d.get("version"),
                "offers": {"@type": "Offer", "price": str(d.get("price", "0")), "priceCurrency": d.get("currency") or "EUR"}}
    elif k in ("article", "blogposting", "newsarticle"):
        node = {"@context": ctx, "@type": "BlogPosting" if k == "blogposting" else "Article",
                "headline": d.get("headline") or d.get("title"), "description": d.get("description"),
                "image": d.get("image"), "datePublished": d.get("date_published"),
                "dateModified": d.get("date_modified") or d.get("date_published"),
                "author": {"@type": "Person", "name": d.get("author")} if d.get("author") else None,
                "publisher": {"@type": "Organization", "name": d.get("publisher"),
                              "logo": {"@type": "ImageObject", "url": d.get("publisher_logo")} if d.get("publisher_logo") else None}
                if d.get("publisher") else None,
                "mainEntityOfPage": d.get("url")}
    elif k == "product":
        node = {"@context": ctx, "@type": "Product", "name": d.get("name"), "description": d.get("description"),
                "image": d.get("image"), "sku": d.get("sku"),
                "brand": {"@type": "Brand", "name": d.get("brand")} if d.get("brand") else None,
                "offers": {"@type": "Offer", "price": str(d["price"]) if d.get("price") is not None else None,
                           "priceCurrency": d.get("currency") or "EUR",
                           "availability": "https://schema.org/" + (d.get("availability") or "InStock"), "url": d.get("url")},
                "aggregateRating": {"@type": "AggregateRating", "ratingValue": d.get("rating"), "reviewCount": d.get("review_count")}
                if d.get("rating") and d.get("review_count") else None}
    elif k in ("breadcrumblist", "breadcrumb", "breadcrumbs"):
        items = d.get("items") or []
        node = {"@context": ctx, "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": it.get("name"), "item": it.get("url")}
            for i, it in enumerate(items) if isinstance(it, dict)]}
    elif k == "service":
        node = {"@context": ctx, "@type": "Service", "name": d.get("name"), "description": d.get("description"),
                "serviceType": d.get("service_type"), "areaServed": d.get("area_served"),
                "provider": {"@type": "Organization", "name": d.get("provider")} if d.get("provider") else None,
                "offers": {"@type": "Offer", "price": str(d["price"]), "priceCurrency": d.get("currency") or "EUR"}
                if d.get("price") is not None else None}
    else:
        raise ValueError(f"Type inconnu : {kind}. Types : {', '.join(SCHEMA_KINDS)}")
    return _prune(node)


_REQUIRED = {
    "localbusiness": ["name", "address", "telephone"],
    "organization": ["name", "url"],
    "website": ["name", "url"],
    "faqpage": ["mainEntity"],
    "softwareapplication": ["name", "offers"],
    "article": ["headline", "author", "datePublished"],
    "product": ["name", "offers"],
    "breadcrumblist": ["itemListElement"],
    "service": ["name", "provider"],
}


async def tool_schema_generate(args: dict, ctx: dict) -> str:
    kind = str(args.get("type") or "").strip()
    data = args.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return "data doit être un objet JSON"
    if not isinstance(data, dict):
        return "data doit être un objet JSON"
    try:
        node = _build_schema(kind, data)
    except ValueError as e:
        return str(e)
    k = re.sub(r"[^a-z]", "", kind.lower())
    k = {"local": "localbusiness", "business": "localbusiness", "faq": "faqpage", "org": "organization",
         "software": "softwareapplication", "app": "softwareapplication", "blogposting": "article",
         "breadcrumb": "breadcrumblist", "breadcrumbs": "breadcrumblist"}.get(k, k)
    missing = [f for f in _REQUIRED.get(k, []) if f not in node]
    snippet = f'<script type="application/ld+json">\n{json.dumps(node, ensure_ascii=False, indent=2)}\n</script>'
    out: dict[str, Any] = {"type": node.get("@type"), "jsonld": node, "html": snippet,
                           "missing_recommended": missing,
                           "validate": "https://search.google.com/test/rich-results"}
    if args.get("save_as"):
        out["exports"] = _save(ctx, args["save_as"], node)
    return _compact(out)


# =====================================================================================
# 5) Generateur llms.txt
# =====================================================================================

_LEGAL_RE = re.compile(r"mentions|legal|privacy|confidentialit|cgu|cgv|cookies|conditions", re.I)


async def tool_llms_txt_generate(args: dict, ctx: dict) -> str:
    url = _norm_url(args.get("url"))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    max_pages = max(1, min(40, int(args.get("max_pages") or 15)))
    robots = await _fetch_robots(_origin(url), ctx)
    pages = [p for p in await _crawl(url, ctx, max_pages, max_depth=2, robots_groups=robots["groups"])
             if "_raw" in p and (p.get("status") or 0) < 400 and "noindex" not in p.get("robots_meta", "")]
    if not pages:
        return "Aucune page HTML exploitable trouvée."
    home = pages[0]
    metas = _metas(home["_raw"])
    title = str(args.get("title") or _meta_value(metas, "og:site_name") or
                re.split(r"\s[—|\-–:]\s", home["title"] or "")[0] or _host(home["url"])).strip()
    summary = str(args.get("summary") or home["description"] or (home["h1"][0] if home["h1"] else "")).strip()
    details = str(args.get("details") or "").strip()

    def line(p: dict) -> str:
        name = (p["h1"][0] if p.get("h1") else "") or p.get("title") or p["url"]
        name = re.sub(r"\s+", " ", name)[:90]
        desc = (p.get("description") or "").strip()
        return f"- [{name}]({p['url']})" + (f": {desc}" if desc else "")

    main, optional = [], []
    for p in pages[1:]:
        (optional if _LEGAL_RE.search(p["url"]) else main).append(line(p))
    sections = [f"# {title}", "", f"> {summary}" if summary else ""]
    if details:
        sections += ["", details]
    # sections du site (H2) de la page d'accueil, utiles pour un one-page
    h2 = [h for h in home.get("h2", []) if len(h) > 3][:12]
    if h2:
        sections += ["", "## Contenu de la page d'accueil", ""] + [f"- {h}" for h in h2]
    sections += ["", "## Pages principales", "", f"- [{title} — accueil]({home['url']})" + (f": {summary}" if summary else "")]
    sections += main
    if optional:
        sections += ["", "## Optional", ""] + optional
    content = "\n".join(s for s in sections if s is not None).strip() + "\n"
    exports = _save(ctx, args.get("save_as") or "llms", content, ext="txt")
    return _compact({
        "pages_used": len(pages), "exports": exports,
        "publish": "Publier ce fichier à la racine du site : " + _origin(home["url"]) + "/llms.txt (texte brut, UTF-8)",
        "llms_txt": content[:4000],
    })


# =====================================================================================
# 6) Mots-cles + ecart concurrent
# =====================================================================================

_SEGMENT_RE = re.compile(r"[.,;:!?()\[\]{}«»\"“”—–|/•·\n\r\t]+")


def _keywords(page: dict, top: int) -> dict:
    # n-grammes calcules par segment : on ne relie pas deux mots separes par
    # une ponctuation (« d'eau, debouchage » ne donne pas « eau debouchage »).
    segments = [[t for t in _tokens(seg) if not t.isdigit()] for seg in _SEGMENT_RE.split(page["_text"])]
    toks = [t for seg in segments for t in seg]
    total = max(1, len(toks))
    uni = Counter(t for t in toks if t not in _STOP and len(t) >= 2)
    bi: Counter = Counter()
    tri: Counter = Counter()
    for seg in segments:
        bi.update(f"{a} {b}" for a, b in zip(seg, seg[1:])
                  if a not in _STOP and b not in _STOP and len(a) > 1 and len(b) > 1)
        tri.update(f"{a} {b} {c}" for a, b, c in zip(seg, seg[1:], seg[2:])
                   if a not in _STOP and c not in _STOP and len(a) > 1 and len(c) > 1)
    title, desc = page["title"].lower(), page["description"].lower()
    h1 = " ".join(page["h1"]).lower()
    h2 = " ".join(page.get("h2", [])).lower()
    path = urlparse(page["url"]).path.lower().replace("-", " ")

    def rows(counter: Counter, n: int, size: int) -> list[dict]:
        out = []
        for kw, c in counter.most_common(n * 3):
            if c < 2:
                continue
            out.append({"keyword": kw, "count": c, "density": round(c * size / total * 100, 2),
                        "in_title": kw in title, "in_h1": kw in h1, "in_description": kw in desc,
                        "in_h2": kw in h2, "in_url": kw in path})
            if len(out) >= n:
                break
        return out

    return {"total_words": total, "unigrams": rows(uni, top, 1), "bigrams": rows(bi, max(5, top // 2), 2),
            "trigrams": rows(tri, max(5, top // 3), 3), "_vocab": set(uni) | set(bi)}


async def tool_seo_keywords(args: dict, ctx: dict) -> str:
    url = _norm_url(args.get("url"))
    if not url.startswith(("http://", "https://")):
        return "URL invalide"
    top = max(5, min(50, int(args.get("top") or 20)))
    try:
        r = await _get(url, ctx)
        page = _page_report(r.text, str(r.url), r.status_code)
    except PermissionError as e:
        return f"Refuse : {e}"
    except httpx.HTTPError as e:
        return f"Erreur : {e}"
    kw = _keywords(page, top)
    title_terms = [w for w in _tokens(page["title"]) if w not in _STOP and len(w) >= 3]
    body = Counter(_tokens(page["_text"]))
    result: dict[str, Any] = {
        "url": page["url"], "title": page["title"], "h1": page["h1"], "description": page["description"],
        "total_words": kw["total_words"],
        "top_keywords": kw["unigrams"], "top_bigrams": kw["bigrams"], "top_trigrams": kw["trigrams"],
        "title_terms_rare_in_body": [w for w in title_terms if body.get(w, 0) < 2],
        "main_keywords_missing_from_title": [k["keyword"] for k in kw["bigrams"][:5] if not k["in_title"]],
    }
    comp_url = _norm_url(args.get("competitor_url"))
    if comp_url.startswith(("http://", "https://")):
        try:
            rc = await _get(comp_url, ctx)
            cpage = _page_report(rc.text, str(rc.url), rc.status_code)
            ckw = _keywords(cpage, top)
            mine = kw["_vocab"]
            gap = [k for k in ckw["bigrams"] + ckw["unigrams"] if k["keyword"] not in mine][:15]
            result["competitor"] = {
                "url": cpage["url"], "title": cpage["title"], "words": ckw["total_words"],
                "top_keywords": [k["keyword"] for k in ckw["unigrams"][:15]],
                "keyword_gap": [{"keyword": k["keyword"], "count": k["count"]} for k in gap],
            }
        except (httpx.HTTPError, PermissionError) as e:
            result["competitor"] = {"url": comp_url, "error": str(e)[:160]}
    if args.get("save_as"):
        result["exports"] = _save(ctx, args["save_as"], result)
    return _compact(result)


# =====================================================================================
# Registre
# =====================================================================================

SEO_TOOLS: dict[str, dict] = {
    "seo_site_audit": {
        "run": tool_seo_site_audit,
        "spec": {
            "name": "seo_site_audit",
            "description": (
                "Audit SEO technique complet d'un site (multi-pages, façon Seobility) : https, redirection "
                "www/non-www, robots.txt, sitemap, soft 404, puis par page title/meta/H1/alt/canonical/"
                "noindex/contenu mince/Open Graph/JSON-LD, doublons et pages en erreur. Score A-F, problèmes "
                "priorisés P0/P1/P2, rapport JSON + Markdown exporté dans le workspace (seo/)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL de départ (page d'accueil)"},
                    "max_pages": {"type": "integer", "description": "Pages à analyser (1-30, défaut 10)"},
                    "save_as": {"type": "string", "description": "Nom du rapport (optionnel)"},
                },
                "required": ["url"],
            },
        },
    },
    "local_seo_audit": {
        "run": tool_local_seo_audit,
        "spec": {
            "name": "local_seo_audit",
            "description": (
                "Audit SEO local d'une entreprise : schéma LocalBusiness et ses champs, cohérence NAP "
                "(nom, adresse, téléphone) sur l'accueil et les pages contact/accès, lien tel:, carte, "
                "horaires, avis, ville dans title/H1. Renvoie un brouillon JSON-LD LocalBusiness pré-rempli "
                "et une checklist Google Business Profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "business_name": {"type": "string", "description": "Nom officiel de l'établissement"},
                    "city": {"type": "string", "description": "Ville ciblée"},
                    "phone": {"type": "string", "description": "Téléphone officiel"},
                    "address": {"type": "string", "description": "Adresse officielle (rue)"},
                    "business_type": {"type": "string", "description": "Type schema.org (Restaurant, Plumber, Dentist…)"},
                    "max_pages": {"type": "integer", "description": "Pages à vérifier (1-8, défaut 4)"},
                    "save_as": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    "geo_audit": {
        "run": tool_geo_audit,
        "spec": {
            "name": "geo_audit",
            "description": (
                "Audit GEO (Generative Engine Optimization) : capacité d'une page à être lue et citée par "
                "ChatGPT, Claude, Perplexity, Gemini, Copilot. Vérifie robots.txt pour 18 bots IA (recherche vs "
                "entraînement), /llms.txt, données structurées (Organization, sameAs, FAQPage, type principal), "
                "contenu citable (questions, FAQ, listes, chiffres, définition) et signaux d'entité/confiance. "
                "Score GEO /100 détaillé + recommandations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "brand": {"type": "string", "description": "Nom de la marque (optionnel)"},
                    "save_as": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    "schema_generate": {
        "run": tool_schema_generate,
        "spec": {
            "name": "schema_generate",
            "description": (
                "Génère un bloc JSON-LD schema.org prêt à coller dans le <head>. Types : localbusiness, "
                "organization, website, faqpage, softwareapplication, article, product, breadcrumblist, service. "
                "Clés de data (selon le type) : name, url, description, telephone, email, image, logo, street, "
                "postal_code, city, country, latitude, longitude, opening_hours [{days, opens, closes}], "
                "same_as [], business_type, price_range, rating, review_count, faq [{question, answer}], "
                "items [{name, url}], headline, author, date_published, date_modified, price, currency, provider."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": SCHEMA_KINDS},
                    "data": {"type": "object", "description": "Données de l'entité"},
                    "save_as": {"type": "string"},
                },
                "required": ["type", "data"],
            },
        },
    },
    "llms_txt_generate": {
        "run": tool_llms_txt_generate,
        "spec": {
            "name": "llms_txt_generate",
            "description": (
                "Génère un fichier llms.txt (standard llmstxt.org) à partir d'un crawl du site : titre, "
                "résumé, sections et liens vers les pages clés avec leur description. Fichier enregistré dans "
                "le workspace (seo/llms.txt) à publier à la racine du site."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_pages": {"type": "integer", "description": "1-40, défaut 15"},
                    "title": {"type": "string"},
                    "summary": {"type": "string", "description": "Résumé en une phrase (sinon meta description)"},
                    "details": {"type": "string", "description": "Paragraphe de contexte optionnel"},
                    "save_as": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    "seo_keywords": {
        "run": tool_seo_keywords,
        "spec": {
            "name": "seo_keywords",
            "description": (
                "Extrait les mots-clés d'une page (1, 2 et 3 mots) avec fréquence, densité et présence dans "
                "title/H1/meta/H2/URL ; signale les termes du title peu repris dans le texte. Avec "
                "competitor_url : écart de mots-clés face à un concurrent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "top": {"type": "integer", "description": "Nombre de mots-clés (5-50, défaut 20)"},
                    "competitor_url": {"type": "string", "description": "Page concurrente à comparer"},
                    "save_as": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
}

SEO_TOOL_COSTS = {
    "seo_site_audit": 120,
    "local_seo_audit": 60,
    "geo_audit": 60,
    "schema_generate": 10,
    "llms_txt_generate": 90,
    "seo_keywords": 30,
}
