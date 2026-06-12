"""Client MCP (Model Context Protocol) minimal — transport Streamable HTTP.

Maltai se connecte a des serveurs MCP distants (URL + jeton optionnel),
decouvre leurs outils (tools/list) et les expose a l'agent (tools/call).

JSON-RPC 2.0 sur POST. Les serveurs peuvent repondre en application/json ou
en text/event-stream : les deux sont geres. La session (Mcp-Session-Id) est
conservee entre les appels d'une meme instance de client.

Adapte au deploiement conteneurise (Coolify) : aucun processus local,
uniquement du HTTP sortant.
"""
from __future__ import annotations

import json
import re

import httpx

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "Maltai", "version": "0.6"}
TIMEOUT = 30
MAX_RESULT_CHARS = 6000


class MCPError(Exception):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_")
    return (s or "srv").lower()


class MCPClient:
    def __init__(self, url: str, auth_token: str = ""):
        self.url = url
        self.session_id: str | None = None
        self._id = 0
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"

    async def _post(self, payload: dict) -> dict | None:
        headers = dict(self._headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(self.url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise MCPError(f"Connexion MCP impossible : {e}") from e
        if r.status_code >= 400:
            raise MCPError(f"Erreur MCP {r.status_code} : {r.text[:200]}")
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if not r.text.strip():
            return None  # notification acceptee (202)
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse(r.text, payload.get("id"))
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise MCPError(f"Reponse MCP invalide : {e}") from e

    @staticmethod
    def _parse_sse(raw: str, expect_id) -> dict | None:
        """Extrait du flux SSE la reponse JSON-RPC correspondant a l'id."""
        last = None
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if expect_id is not None and obj.get("id") == expect_id:
                return obj
            last = obj
        return last

    async def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        resp = await self._post(payload)
        if resp is None:
            raise MCPError(f"Pas de reponse pour {method}")
        if "error" in resp:
            err = resp["error"]
            raise MCPError(f"{method} : {err.get('message', err)}")
        return resp.get("result", {})

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._post(payload)

    async def initialize(self) -> dict:
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        try:
            await self.notify("notifications/initialized")
        except MCPError:
            pass  # certains serveurs n'attendent pas la notification
        return result

    async def list_tools(self) -> list[dict]:
        result = await self.request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(f"[{item.get('type', 'contenu')} non textuel]")
        text = "\n".join(parts) or json.dumps(result, ensure_ascii=False)[:500]
        if result.get("isError"):
            text = f"[erreur outil MCP] {text}"
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "…[tronque]"
        return text


# --- Agregation pour l'agent ------------------------------------------------

async def load_mcp_tools(servers: list[dict]) -> tuple[list[dict], dict]:
    """Connecte les serveurs MCP actifs et agrege leurs outils.

    Retourne (specs_openai, dispatch) :
      specs_openai : specs au format function-calling OpenAI
      dispatch     : nom_prefixe -> {"client": MCPClient, "tool": nom_original}
    Un serveur injoignable est ignore (best-effort, l'agent continue).
    """
    specs: list[dict] = []
    dispatch: dict[str, dict] = {}
    for srv in servers:
        client = MCPClient(srv["url"], srv.get("auth_token", ""))
        try:
            await client.initialize()
            tools = await client.list_tools()
        except MCPError:
            continue
        prefix = f"mcp_{slugify(srv['name'])}"
        for t in tools:
            name = f"{prefix}_{slugify(t.get('name', 'tool'))}"[:64]
            if name in dispatch:
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": (t.get("description") or t.get("name", ""))[:1000],
                    "parameters": t.get("inputSchema")
                    or {"type": "object", "properties": {}},
                },
            })
            dispatch[name] = {"client": client, "tool": t.get("name", "")}
    return specs, dispatch
