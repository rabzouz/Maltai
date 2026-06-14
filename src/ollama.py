"""Client minimal pour l'API native Ollama."""
from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.config import settings


class OllamaError(Exception):
    pass


def configured_base_url() -> str:
    return settings.OLLAMA_BASE_URL.rstrip("/")


def _docker_gateway_urls() -> list[str]:
    route = Path("/proc/net/route")
    if not route.exists():
        return []
    urls = []
    try:
        for line in route.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3 or fields[1] != "00000000":
                continue
            gateway_hex = fields[2]
            raw = bytes.fromhex(gateway_hex)
            host = socket.inet_ntoa(raw[::-1])
            urls.append(f"http://{host}:11434")
    except OSError:
        return []
    return urls


def _candidate_base_urls() -> list[str]:
    configured = configured_base_url()
    urls = [configured]
    parsed = urlparse(configured)
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        urls.extend(["http://host.docker.internal:11434", *_docker_gateway_urls()])
    seen = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def _url(base_url: str, path: str) -> str:
    return f"{base_url}{path}"


async def _request_json(method: str, path: str, payload: dict | None = None) -> dict:
    last_error = ""
    tried = []
    for base_url in _candidate_base_urls():
        tried.append(base_url)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(method, _url(base_url, path), json=payload)
        except httpx.HTTPError as e:
            last_error = str(e)
            continue
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error") or resp.text
            except json.JSONDecodeError:
                detail = resp.text
            raise OllamaError(detail or f"Erreur Ollama HTTP {resp.status_code}")
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise OllamaError("Reponse Ollama invalide") from e
    suffix = f" URLs essayees: {', '.join(tried)}"
    if last_error:
        suffix += f" ({last_error})"
    raise OllamaError(f"Ollama injoignable.{suffix}")


async def _stream_pull(base_url: str, name: str) -> AsyncIterator[dict]:
    payload = {"model": name, "stream": True}
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", _url(base_url, "/api/pull"), json=payload) as resp:
                if resp.status_code >= 400:
                    text = await resp.aread()
                    raise OllamaError(text.decode(errors="replace") or f"Erreur Ollama HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        yield {"status": line}
    except httpx.HTTPError as e:
        raise OllamaError(str(e)) from e


async def list_models() -> list[dict]:
    data = await _request_json("GET", "/api/tags")
    models = data.get("models") or []
    return [
        {
            "name": m.get("name") or m.get("model", ""),
            "model": m.get("model") or m.get("name", ""),
            "size": m.get("size", 0),
            "modified_at": m.get("modified_at", ""),
            "digest": m.get("digest", ""),
            "details": m.get("details") or {},
        }
        for m in models
        if m.get("name") or m.get("model")
    ]


async def delete_model(name: str) -> None:
    await _request_json("DELETE", "/api/delete", {"model": name})


async def pull_model(name: str) -> AsyncIterator[dict]:
    tried = []
    last_error = ""
    for base_url in _candidate_base_urls():
        tried.append(base_url)
        try:
            async for item in _stream_pull(base_url, name):
                yield item
            return
        except OllamaError as e:
            last_error = str(e)
            continue
    suffix = f" URLs essayees: {', '.join(tried)}"
    if last_error:
        suffix += f" ({last_error})"
    raise OllamaError(f"Ollama injoignable.{suffix}")
