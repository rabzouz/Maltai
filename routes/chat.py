"""Route de chat : mode simple (stream direct) ou mode agent (outils),
avec memoire vectorielle (recall avant, remember apres).

SSE events : memory, delta, tool, tool_result, error, done.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import database as db
from core import plans
from src import agent, llm, memory
from src.prompts import SYSTEM_PROMPT
from src.tools import WORKSPACE

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    session_id: str
    provider_id: str
    model: str
    content: str
    temperature: float = 0.7
    agent: bool = False
    attachment_ids: list[str] = []
    enabled_tools: list[str] | None = None


def _safe_workspace_user(user_id: str | None) -> str:
    uid = user_id or "shared"
    return "".join(c for c in str(uid) if c.isalnum() or c in "-_")[:32] or "shared"


def _safe_workspace_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[:120] or "fichier"


def _copy_attachment_to_workspace(up: dict, user_id: str | None) -> str | None:
    if not up.get("text_extract"):
        return None
    source = Path(up["path"])
    if not source.is_file():
        return None
    user_dir = WORKSPACE / _safe_workspace_user(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / _safe_workspace_filename(up["filename"])
    try:
        target.write_bytes(source.read_bytes())
    except OSError:
        return None
    return str(target.relative_to(user_dir)).replace("\\", "/")


def _build_attachments(ids: list[str], user_id: str | None) -> tuple[str, list[dict]]:
    """Retourne (contexte_texte, blocs_images) pour les pieces jointes."""
    context_parts: list[str] = []
    image_blocks: list[dict] = []
    for uid in ids[:8]:
        up = db.get_upload(uid)
        if not up:
            continue
        if up["mime"].startswith("image/"):
            try:
                raw = Path(up["path"]).read_bytes()
            except OSError:
                continue
            b64 = base64.b64encode(raw).decode()
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{up['mime']};base64,{b64}"},
            })
        elif up["text_extract"]:
            workspace_path = _copy_attachment_to_workspace(up, user_id)
            path_hint = (
                f"\nChemin workspace pour read_file : {workspace_path}"
                if workspace_path else ""
            )
            context_parts.append(
                f"--- Fichier joint : {up['filename']} ---{path_hint}\n{up['text_extract']}"
            )
    return "\n\n".join(context_parts), image_blocks


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(body: ChatIn, request: Request):
    provider = db.get_provider(body.provider_id)
    if not provider:
        raise HTTPException(404, "Provider introuvable")

    user = getattr(request.state, "user", None)
    user_id = user["id"] if user else None
    is_admin = bool(user and user.get("is_admin"))
    plan = plans.normalize_plan(user.get("plan") if user else None, is_admin)
    if body.agent and not plans.can_use_tools(plan, is_admin):
        raise HTTPException(403, "Plan premium requis pour utiliser les outils de l'agent")

    history = db.list_messages(body.session_id)
    stored_content = body.content
    if body.attachment_ids:
        names = [db.get_upload(u)["filename"] for u in body.attachment_ids if db.get_upload(u)]
        if names:
            stored_content += "\n[fichiers joints : " + ", ".join(names) + "]"
    db.add_message(body.session_id, "user", stored_content)

    if not history:
        title = body.content.strip().splitlines()[0][:60] or "Discussion"
        db.rename_session(body.session_id, title)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)

    file_context, image_blocks = _build_attachments(body.attachment_ids, user_id)
    if image_blocks:
        # Message multimodal (modeles vision) : texte + image(s) en base64.
        user_content: object = [{"type": "text", "text": body.content}, *image_blocks]
    else:
        user_content = body.content
    messages.append({"role": "user", "content": user_content})
    if file_context:
        messages.insert(
            len(messages) - 1,
            {"role": "system", "content": "Contenu des fichiers joints par l'utilisateur :\n" + file_context},
        )

    # --- Recall : souvenirs pertinents d'autres sessions ---
    recalled = await memory.recall(
        provider, user_id, body.content, exclude_session=body.session_id
    )
    if recalled:
        ctx = memory.format_context(recalled)
        messages = [{"role": "system", "content": ctx}, *messages]

    async def finalize(answer: str):
        if answer:
            db.add_message(body.session_id, "assistant", answer)
        # Remember : on memorise le message user et la reponse (best-effort).
        await memory.remember(provider, user_id, body.session_id, "user", body.content)
        await memory.remember(provider, user_id, body.session_id, "assistant", answer)

    async def gen_simple():
        if recalled:
            yield _sse("memory", {"count": len(recalled)})
        full = []
        try:
            async for piece in llm.stream_chat(
                provider["base_url"], provider["api_key"], body.model,
                messages, body.temperature,
            ):
                full.append(piece)
                yield _sse("delta", {"content": piece})
        except llm.LLMError as e:
            yield _sse("error", {"message": str(e)})
            return
        answer = "".join(full)
        await finalize(answer)
        yield _sse("done", {"length": len(answer)})

    async def gen_agent():
        if recalled:
            yield _sse("memory", {"count": len(recalled)})
        full = []
        async for ev, data in agent.run_agent(
            provider, body.model, messages, is_admin, body.temperature,
            user_id=user_id, enabled_tools=body.enabled_tools, plan=plan,
        ):
            if ev == "delta":
                full.append(data["content"])
                yield _sse("delta", data)
            elif ev == "tool":
                yield _sse("tool", data)
            elif ev == "tool_result":
                yield _sse("tool_result", data)
            elif ev == "agent_error":
                yield _sse("error", {"message": data["message"]})
        answer = "".join(full)
        await finalize(answer)
        yield _sse("done", {"length": len(answer)})

    gen = gen_agent if body.agent else gen_simple
    return StreamingResponse(gen(), media_type="text/event-stream")
