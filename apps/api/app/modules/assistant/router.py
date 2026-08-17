"""Chat and Gemini Live voice. The API key stays on this process."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.services.assistant import (
    DATA_TOOLS,
    LIVE_WS,
    chat,
    compact_snapshot,
    converse,
    extract_actions,
    gemini_keys,
    live_model_chain,
    live_setup_message,
    mark_key_limited,
    ordered_keys,
    run_tool,
)

log = logging.getLogger("sthiraroute.assistant")

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatIn(BaseModel):
    message: str
    view: str | None = None
    selected: int | None = None
    decision: dict | None = None
    kpis: dict | None = None
    vehicles: list | None = None
    shipments: list | None = None
    overlays: list | None = None
    # The browser owns the list of things it can point at, so the model is
    # never offered a target that is not on screen.
    targets: list[str] | None = None
    target_brief: str | None = None


@router.get("/status")
def status():
    keys = gemini_keys()
    return {
        "configured": bool(keys),
        "keys": len(keys),
        "model": settings.gemini_model,
        "live_model": settings.gemini_live_model,
        "hint": None if keys else "Set GEMINI_API_KEY (and optional GEMINI_API_KEYS) in apps/api/.env",
    }


@router.post("/chat")
async def assistant_chat(body: ChatIn, db: Session = Depends(get_db)):
    extra = body.model_dump(exclude={"message"})
    return await chat(db, body.message, extra)


@router.post("/chat/stream")
async def assistant_chat_stream(body: ChatIn, db: Session = Depends(get_db)):
    """Same exchange, delivered as it happens.

    The highlight lands while the sentence is still being written, which is the
    whole point: the officer points first and explains as it goes.
    """
    extra = body.model_dump(exclude={"message"})

    async def events():
        try:
            async for ev in converse(db, body.message, extra, stream=True):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/live")
async def live(ws: WebSocket, db: Session = Depends(get_db)):
    await ws.accept()
    keys = ordered_keys()
    if not keys:
        await ws.send_json({
            "type": "error",
            "message": "GEMINI_API_KEY is missing on the API.",
        })
        await ws.close()
        return

    import websockets

    gemini = None
    try:
        first = await ws.receive_json()
        extra = first.get("context") or {}
        snap = compact_snapshot(db, extra)

        async def dial(model: str, uri: str):
            try:
                sock = await websockets.connect(
                    uri,
                    additional_headers={"Content-Type": "application/json"},
                    max_size=8 * 1024 * 1024,
                    open_timeout=20,
                )
            except TypeError:
                sock = await websockets.connect(uri, max_size=8 * 1024 * 1024, open_timeout=20)
            await sock.send(json.dumps(
                live_setup_message(
                    snap,
                    extra.get("targets"),
                    extra.get("target_brief") or "",
                    first.get("resume"),
                    model=model,
                )
            ))
            ack = await asyncio.wait_for(sock.recv(), timeout=20)
            body = json.loads(ack) if isinstance(ack, (str, bytes)) else ack
            if body.get("setupComplete") is None:
                await sock.close()
                raise RuntimeError(str(body)[:200])
            return sock

        chain = live_model_chain()
        chosen = None
        last_err: Exception | None = None
        for key in keys:
            uri = f"{LIVE_WS}?key={key}"
            for model in chain:
                try:
                    gemini = await dial(model, uri)
                    chosen = model
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    msg = str(e).lower()
                    log.warning("live key …%s model %s refused: %s", key[-6:], model, str(e)[:200])
                    if "429" in msg or "quota" in msg or "resource_exhausted" in msg:
                        mark_key_limited(key)
                        break
                    if "401" in msg or "403" in msg or "api key" in msg:
                        mark_key_limited(key, bad=True)
                        break
            if chosen:
                break
        if not chosen:
            raise last_err or RuntimeError("live: every key and model refused")
        await ws.send_json({"type": "ready", "model": chosen})

        # The snapshot the browser sent most recently. It rides along with the
        # next thing the dispatcher actually says instead of being injected as
        # a fake user turn every twenty seconds, which used to make the officer
        # answer questions nobody had asked.
        latest = {"context": extra}

        async def client_to_gemini():
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "audio" and msg.get("data"):
                    await gemini.send(json.dumps({
                        "realtimeInput": {
                            "audio": {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": msg["data"],
                            }
                        }
                    }))
                elif kind == "text" and msg.get("text"):
                    board = json.dumps(latest["context"], default=str)[:3000]
                    await gemini.send(json.dumps({
                        "clientContent": {
                            "turns": [{
                                "role": "user",
                                "parts": [
                                    {"text": f"[board right now]\n{board}"},
                                    {"text": msg["text"]},
                                ],
                            }],
                            "turnComplete": True,
                        }
                    }))
                elif kind == "context":
                    latest["context"] = msg.get("context") or {}
                elif kind == "end":
                    await gemini.send(json.dumps({
                        "realtimeInput": {"audioStreamEnd": True}
                    }))
                elif kind == "close":
                    break

        async def gemini_to_client():
            async for raw in gemini:
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                if data.get("setupComplete") is not None:
                    continue

                resume = data.get("sessionResumptionUpdate")
                if resume and resume.get("resumable") and resume.get("newHandle"):
                    await ws.send_json({"type": "resume", "handle": resume["newHandle"]})

                if data.get("goAway"):
                    await ws.send_json({
                        "type": "goaway",
                        "in_s": (data["goAway"] or {}).get("timeLeft"),
                    })

                sc = data.get("serverContent") or {}
                if sc.get("interrupted"):
                    # Barge-in: whatever is queued in the browser is now stale.
                    await ws.send_json({"type": "interrupted"})

                heard = (sc.get("inputTranscription") or {}).get("text")
                if heard:
                    await ws.send_json({"type": "heard", "text": heard})
                said = (sc.get("outputTranscription") or {}).get("text")
                if said:
                    await ws.send_json({"type": "said", "text": said})

                mt = sc.get("modelTurn") or {}
                parts = mt.get("parts") or []
                text, actions = extract_actions(parts)
                calls = list((data.get("toolCall") or {}).get("functionCalls") or [])
                for fc in calls:
                    actions.append({"name": fc.get("name"), "args": fc.get("args") or {}})

                for p in parts:
                    inline = p.get("inlineData") if isinstance(p, dict) else None
                    if inline and inline.get("data"):
                        await ws.send_json({
                            "type": "audio",
                            "mime": inline.get("mimeType") or "audio/pcm;rate=24000",
                            "data": inline["data"],
                        })
                if text:
                    await ws.send_json({"type": "transcript", "role": "model", "text": text})
                for act in actions:
                    # Data tools are answered here, not on screen.
                    if act.get("name") not in DATA_TOOLS:
                        await ws.send_json({"type": "action", **act})

                # A toolCall that never gets a response leaves the model waiting
                # for the world to answer — the voice would point at the clock
                # and then go silent mid-sentence.
                if calls:
                    await gemini.send(json.dumps({
                        "toolResponse": {
                            "functionResponses": [
                                {
                                    "id": fc.get("id"),
                                    "name": fc.get("name"),
                                    # A data tool answers with the board's real
                                    # numbers; a screen action only needs an ack.
                                    "response": (
                                        run_tool(
                                            db,
                                            fc.get("name"),
                                            fc.get("args") or {},
                                            latest["context"],
                                        )
                                        if fc.get("name") in DATA_TOOLS
                                        else {"ok": True}
                                    ),
                                }
                                for fc in calls
                            ]
                        }
                    }))

                if sc.get("turnComplete"):
                    await ws.send_json({"type": "turn_complete"})
                err = data.get("error")
                if err:
                    await ws.send_json({
                        "type": "error",
                        "message": err.get("message") if isinstance(err, dict) else str(err),
                    })

        await asyncio.gather(client_to_gemini(), gemini_to_client())
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.exception("live session failed")
        try:
            await ws.send_json({"type": "error", "message": str(e)[:300]})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if gemini is not None:
            await gemini.close()
