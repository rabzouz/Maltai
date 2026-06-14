"""Client minimal pour l'API native Ollama."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from core.config import settings


class OllamaError(Exception):
    pass


def _base_url() -> str:
    return settings.OLLAMA_BASE_URL.rstrip("/")


def _url(path: str) -> str:
    return f"{_base_url()}{path}"


async def _request_json(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, _url(path), json=payload)
    except httpx.HTTPError as e:
        raise OllamaError(f"Ollama injoignable ({_base_url()})") from e
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
    payload = {"model": name, "stream": True}
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", _url("/api/pull"), json=payload) as resp:
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
        raise OllamaError(f"Ollama injoignable ({_base_url()})") from e
