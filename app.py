# -*- coding: utf-8 -*-
"""
Backend de la web de un negocio de demo de MONBI (Raskigan, Wandelus, Insurtia, Raym Eban).

La web es un SISTEMA DEL CLIENTE: no habla con las tablas de MONBI. Registra lo que pasa en la tienda en SU
Serpis (la visita, los eventos, los comentarios) y pide al Salesman de MONBI (`engines`) que converse, con
la credencial del negocio. Es la misma frontera que la web de MONBI Labs (`monbi-website`).

Rutas (todas relativas, para poder servirse bajo `demos.monbi.ai/websites/<negocio>/`):
  GET  /                      la página (static/index.html) y sus ficheros
  GET  api/config             nombre, negocio, contenedor de GTM (público)
  GET  api/catalog            el catálogo del negocio (data/catalog.json, exportado de su Serpis)
  POST api/session            abre la visita en Serpis (`/sessions`)
  POST api/track              eventos de la visita (`/events`)
  POST api/chat               un turno con el Salesman (NDJSON en streaming, desde `engines`)
  POST api/register           alta: los datos se guardan AQUÍ (el CRM del negocio); a Serpis va `signin`
  GET  api/forum              hilos y comentarios (guardados aquí; cada comentario va también a Serpis)
  POST api/forum              un hilo nuevo
  POST api/comment            un comentario o una opinión sobre un hilo o un producto

Variables de entorno:
  BUSINESS            slug del negocio (raskigan, wandelus, insurtia, raymeban)
  SERPIS_URL          la API de su Serpis, p. ej. http://serpis-raskigan.railway.internal:8080/v1
  SERPIS_INGEST_TOKEN credencial de SÓLO ESCRITURA de su Serpis (o SERPIS_TOKEN si no hay)
  ENGINES_URL         https://api.monbi.ai
  ENGINES_TOKEN       la credencial del negocio en MONBI (la de MONBI_TENANT_TOKENS)
  GTM_ID              (opcional) el contenedor de Google Tag Manager de la web
  DATA_DIR            (opcional) dónde guardar la base local; por defecto ./var
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
BUSINESS = os.getenv("BUSINESS", "demo")
SERPIS_URL = os.getenv("SERPIS_URL", "").rstrip("/")
SERPIS_TOKEN = os.getenv("SERPIS_INGEST_TOKEN") or os.getenv("SERPIS_TOKEN") or ""
ENGINES_URL = os.getenv("ENGINES_URL", "https://api.monbi.ai").rstrip("/")
ENGINES_TOKEN = os.getenv("ENGINES_TOKEN", "")
GTM_ID = os.getenv("GTM_ID", "")
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "site.db"

app = FastAPI(title=f"{BUSINESS} website", docs_url=None, redoc_url=None)


# ── Base local: el «CRM» de la web (contactos) y su foro ─────────────────────────
def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


with _db() as _c:
    _c.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            customer_key TEXT PRIMARY KEY, kind TEXT, name TEXT, email TEXT, phone TEXT, company TEXT,
            details TEXT, session_key TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY, category TEXT, title TEXT, body TEXT, author TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY, target TEXT, author TEXT, text TEXT, rating REAL, created_at TEXT);
    """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _customer_key(email: str) -> str:
    """Clave seudónima del contacto: lo que viaja a Serpis. El correo se queda aquí."""
    return "c_" + hashlib.sha256(f"{BUSINESS}:{email.strip().lower()}".encode()).hexdigest()[:24]


# ── Serpis ───────────────────────────────────────────────────────────────────
def _serpis(path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Escribe en el Serpis del negocio. Un fallo NO tumba la página: se devuelve y se registra."""
    if not SERPIS_URL:
        return {"skipped": "SERPIS_URL not set"}
    try:
        r = httpx.post(f"{SERPIS_URL}/{path}", json=rows, timeout=8,
                       headers={"Authorization": f"Bearer {SERPIS_TOKEN}"} if SERPIS_TOKEN else {})
        if r.status_code >= 400:
            print(f"[serpis] {path} {r.status_code} {r.text[:200]}", flush=True)
        return {"status": r.status_code}
    except Exception as exc:                                            # noqa: BLE001
        print(f"[serpis] {path} failed: {exc}", flush=True)
        return {"error": str(exc)[:120]}


def _events(session_key: str, items: list[dict[str, Any]], customer_key: str | None = None,
            consent: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    for it in items:
        if not it.get("event"):
            continue
        rows.append({
            "session_key": session_key, "visitor_key": it.get("visitor_key"),
            "customer_key": customer_key or it.get("customer_key"),
            "event": str(it["event"])[:80], "page_type": it.get("page"), "element": it.get("element"),
            "value_json": it.get("value") if isinstance(it.get("value"), dict) else None,
            "occurred_at": it.get("at") or _now(),
            "consent_analytics": (consent or {}).get("analytics"),
            "consent_marketing": (consent or {}).get("marketing"),
            "source": "website",
        })
    return _serpis("events", rows) if rows else {"accepted": 0}


# ── API ──────────────────────────────────────────────────────────────────────
@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"business": BUSINESS, "gtm_id": GTM_ID or None}


@app.get("/api/catalog")
def catalog() -> Any:
    f = ROOT / "data" / "catalog.json"
    if not f.exists():
        raise HTTPException(404, "catalog not exported")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.post("/api/session")
async def session(req: Request) -> dict[str, Any]:
    b = await req.json()
    row = {k: b.get(k) for k in ("session_key", "visitor_key", "landing_page_type", "referrer_host",
                                 "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                                 "device", "consent_analytics", "consent_marketing")}
    if not row["session_key"]:
        raise HTTPException(422, "session_key required")
    row["started_at"] = _now()
    row["source"] = "website"
    return _serpis("sessions", [{k: v for k, v in row.items() if v is not None}])


@app.post("/api/track")
async def track(req: Request) -> dict[str, Any]:
    b = await req.json()
    if not b.get("session_key"):
        raise HTTPException(422, "session_key required")
    return _events(b["session_key"], b.get("events") or [], b.get("customer_key"), b.get("consent"))


@app.post("/api/chat")
async def chat(req: Request) -> StreamingResponse:
    b = await req.json()
    if not (b.get("session_key") and b.get("message")):
        raise HTTPException(422, "session_key and message required")
    payload = {"session_id": b["session_key"],
               "conversation_id": b.get("conversation_id") or f"web_{b['session_key']}",
               "message": str(b["message"])[:2000]}
    headers = {"Authorization": f"Bearer {ENGINES_TOKEN}"} if ENGINES_TOKEN else {}

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=120) as cli:
                async with cli.stream("POST", f"{ENGINES_URL}/api/salesman/chat/stream",
                                      json=payload, headers=headers) as r:
                    if r.status_code >= 400:
                        yield json.dumps({"error": f"chat unavailable ({r.status_code})"}) + "\n"
                        return
                    async for line in r.aiter_lines():
                        if line.strip():
                            yield line + "\n"
        except Exception as exc:                                        # noqa: BLE001
            yield json.dumps({"error": f"chat unavailable ({type(exc).__name__})"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/register")
async def register(req: Request) -> dict[str, Any]:
    b = await req.json()
    email = (b.get("email") or "").strip()
    if "@" not in email:
        raise HTTPException(422, "a valid email is required")
    ck = _customer_key(email)
    with _db() as c:
        c.execute("""INSERT INTO contacts (customer_key, kind, name, email, phone, company, details,
                     session_key, created_at) VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(customer_key) DO UPDATE SET kind=excluded.kind, name=excluded.name,
                     phone=excluded.phone, company=excluded.company, details=excluded.details""",
                  (ck, b.get("kind"), b.get("name"), email, b.get("phone"), b.get("company"),
                   json.dumps(b.get("details") or {}, ensure_ascii=False), b.get("session_key"), _now()))
    if b.get("session_key"):
        _events(b["session_key"], [{"event": "signin", "element": b.get("kind"),
                                    "value": {"kind": b.get("kind"), "newsletter": bool(b.get("newsletter"))}}],
                customer_key=ck, consent=b.get("consent"))
    return {"customer_key": ck}


@app.get("/api/forum")
def forum() -> dict[str, Any]:
    with _db() as c:
        threads = [dict(r) for r in c.execute("SELECT * FROM threads ORDER BY created_at DESC LIMIT 200")]
        comments = [dict(r) for r in c.execute("SELECT * FROM comments ORDER BY created_at DESC LIMIT 1000")]
    return {"threads": threads, "comments": comments}


@app.post("/api/forum")
async def new_thread(req: Request) -> dict[str, Any]:
    b = await req.json()
    if not (b.get("title") or "").strip():
        raise HTTPException(422, "title required")
    tid = "t_" + uuid.uuid4().hex[:12]
    with _db() as c:
        c.execute("INSERT INTO threads VALUES (?,?,?,?,?,?)",
                  (tid, b.get("category"), b["title"][:200], (b.get("body") or "")[:4000],
                   (b.get("author") or "anónimo")[:60], _now()))
    if b.get("session_key"):
        _events(b["session_key"], [{"event": "forum_post", "element": tid, "value": {"category": b.get("category")}}],
                customer_key=b.get("customer_key"))
    return {"id": tid}


@app.post("/api/comment")
async def comment(req: Request) -> dict[str, Any]:
    b = await req.json()
    if not (b.get("text") or "").strip() and b.get("rating") is None:
        raise HTTPException(422, "text or rating required")
    cid = "m_" + uuid.uuid4().hex[:12]
    with _db() as c:
        c.execute("INSERT INTO comments VALUES (?,?,?,?,?,?)",
                  (cid, b.get("target"), (b.get("author") or "anónimo")[:60], (b.get("text") or "")[:4000],
                   b.get("rating"), _now()))
    _serpis("comments", [{"comment_key": cid, "session_key": b.get("session_key"),
                          "customer_key": b.get("customer_key"), "target": b.get("target"),
                          "text": (b.get("text") or "")[:4000], "rating": b.get("rating"),
                          "occurred_at": _now(), "source": "website"}])
    return {"id": cid}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "business": BUSINESS, "serpis": bool(SERPIS_URL), "engines": bool(ENGINES_TOKEN)}


# ── La página ────────────────────────────────────────────────────────────────
STATIC = ROOT / "static"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
