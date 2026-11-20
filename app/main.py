"""Lehmus Chat — a small local web UI for ConfidentialMind Model Gateway models.

API keys live only in config.json on this machine and are never sent to the
browser. The browser talks to this server; this server talks to the gateway.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "chat.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # gateway limit is 32 MiB per request
MAX_FILE_TEXT_CHARS = 200_000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx", ".py", ".java", ".c",
    ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat",
    ".ps1", ".sql", ".r", ".m", ".tex", ".log", ".ini", ".toml", ".cfg", ".kt",
    ".swift", ".scala", ".lua", ".pl", ".ipynb", ".srt", ".vtt", ".rst",
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


# --------------------------------------------------------------------------- config

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"Missing {CONFIG_PATH.name}. Place config.json with the API URL and keys in the project folder."
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["api_base_url"] = cfg["api_base_url"].rstrip("/")
    if cfg["api_base_url"].endswith("/v1"):
        cfg["api_base_url"] = cfg["api_base_url"][:-3]
    return cfg


CONFIG: dict[str, Any] = {}
# model id -> {"key": str, "display_name": str, "input_modalities": [...], "max_model_len": int|None}
MODELS: dict[str, dict[str, Any]] = {}
MODEL_ERRORS: list[str] = []


async def discover_models() -> None:
    """Ask the gateway which models each key may invoke and build the id -> key map."""
    found: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    base = CONFIG["api_base_url"]
    async with httpx.AsyncClient(timeout=20) as client:
        for entry in CONFIG.get("api_keys", []):
            key = entry["key"]
            label = entry.get("label") or key[:12] + "…"
            allowed = set(entry.get("models") or [])
            try:
                r = await client.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                listed = r.json().get("data", [])
            except httpx.HTTPStatusError as e:  # keep going with other keys
                hint = {401: "key invalid or revoked", 403: "key lacks permission"}.get(e.response.status_code, "")
                errors.append(f"API key '{label}': could not list models (HTTP {e.response.status_code} {hint})".strip())
                listed = [{"id": m} for m in allowed]
            except Exception as e:
                errors.append(f"API key '{label}': could not reach the gateway ({type(e).__name__})")
                listed = [{"id": m} for m in allowed]  # fall back to the configured ids
            for m in listed:
                mid = m.get("id")
                if not mid or (allowed and mid not in allowed) or mid in found:
                    continue
                found[mid] = {
                    "key": key,
                    "display_name": m.get("display_name") or mid,
                    "input_modalities": m.get("input_modalities") or ["text"],
                    "max_model_len": m.get("max_model_len"),
                }
    MODELS.clear()
    MODELS.update(dict(sorted(found.items(), key=lambda kv: kv[1]["display_name"].lower())))
    MODEL_ERRORS[:] = errors


# --------------------------------------------------------------------------- database

_db_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachments TEXT NOT NULL DEFAULT '[]',
                model TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                mime TEXT NOT NULL,
                kind TEXT NOT NULL,          -- 'image' | 'text'
                path TEXT NOT NULL,
                text TEXT,
                size INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
            """
        )


def file_info(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "filename": row["filename"], "mime": row["mime"],
            "kind": row["kind"], "size": row["size"]}


# --------------------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(_: FastAPI):
    CONFIG.update(load_config())
    init_db()
    await discover_models()
    yield


app = FastAPI(title="Lehmus Chat", lifespan=lifespan)


@app.get("/api/models")
async def list_models(refresh: bool = False):
    if refresh or not MODELS:
        await discover_models()
    return {
        "models": [
            {"id": mid, "display_name": m["display_name"],
             "vision": "image" in m["input_modalities"], "max_model_len": m["max_model_len"]}
            for mid, m in MODELS.items()
        ],
        "default_model": CONFIG.get("default_model"),
        "errors": MODEL_ERRORS,
    }


class NewConversation(BaseModel):
    model: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    model: str | None = None


@app.get("/api/conversations")
def list_conversations(q: str | None = None):
    with db() as c:
        if q:
            rows = c.execute(
                """SELECT DISTINCT c.* FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.title LIKE ? OR m.content LIKE ?
                   ORDER BY c.updated_at DESC""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/conversations")
def create_conversation(body: NewConversation):
    now = time.time()
    cid = uuid.uuid4().hex
    with _db_lock, db() as c:
        c.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, "New chat", body.model, now, now))
    return {"id": cid, "title": "New chat", "model": body.model, "created_at": now, "updated_at": now}


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    with db() as c:
        conv = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        msgs = c.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)
        ).fetchall()
        out = []
        for m in msgs:
            ids = json.loads(m["attachments"])
            files = []
            if ids:
                q = ",".join("?" * len(ids))
                files = [file_info(f) for f in c.execute(f"SELECT * FROM files WHERE id IN ({q})", ids)]
            out.append({"id": m["id"], "role": m["role"], "content": m["content"],
                        "model": m["model"], "attachments": files, "created_at": m["created_at"]})
    return {**dict(conv), "messages": out}


@app.patch("/api/conversations/{cid}")
def patch_conversation(cid: str, body: ConversationPatch):
    with _db_lock, db() as c:
        if body.title is not None:
            c.execute("UPDATE conversations SET title=? WHERE id=?", (body.title.strip()[:200] or "Untitled", cid))
        if body.model is not None:
            c.execute("UPDATE conversations SET model=? WHERE id=?", (body.model, cid))
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    with _db_lock, db() as c:
        c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    return {"ok": True}


# --------------------------------------------------------------------------- uploads

def extract_text(path: Path, filename: str, mime: str) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n\n".join(
            f"--- Page {i + 1} ---\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages)
        )
    if ext == ".docx":
        import zipfile
        import re
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        raw = path.read_bytes()
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return None


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    filename = Path(file.filename or "file").name
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fid = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{fid}{Path(filename).suffix.lower()}"
    path.write_bytes(data)

    if mime in IMAGE_MIMES:
        kind, text = "image", None
    else:
        try:
            text = extract_text(path, filename, mime)
        except Exception as e:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"Could not read {filename}: {e}")
        if text is None:
            path.unlink(missing_ok=True)
            raise HTTPException(
                415, f"Unsupported file type: {filename}. Supported: images, PDF, DOCX and text/code files."
            )
        kind = "text"

    with _db_lock, db() as c:
        c.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)",
                  (fid, filename, mime, kind, str(path), text, len(data), time.time()))
    return {"id": fid, "filename": filename, "mime": mime, "kind": kind, "size": len(data)}


@app.get("/api/uploads/{fid}")
def get_upload(fid: str):
    with db() as c:
        row = c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return FileResponse(row["path"], media_type=row["mime"], filename=row["filename"])


# --------------------------------------------------------------------------- chat

class SendMessage(BaseModel):
    content: str
    model: str
    attachments: list[str] = []
    regenerate: bool = False  # replace the last assistant answer instead of adding a user message


def build_upstream_messages(c: sqlite3.Connection, cid: str, vision: bool) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if CONFIG.get("system_prompt"):
        msgs.append({"role": "system", "content": CONFIG["system_prompt"]})
    for m in c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)):
        ids = json.loads(m["attachments"])
        if not ids:
            msgs.append({"role": m["role"], "content": m["content"]})
            continue
        q = ",".join("?" * len(ids))
        files = c.execute(f"SELECT * FROM files WHERE id IN ({q})", ids).fetchall()
        text = m["content"]
        images = []
        for f in files:
            if f["kind"] == "text":
                body = f["text"] or ""
                if len(body) > MAX_FILE_TEXT_CHARS:
                    body = body[:MAX_FILE_TEXT_CHARS] + "\n[... truncated ...]"
                text += f"\n\n<file name=\"{f['filename']}\">\n{body}\n</file>"
            elif vision:
                b64 = base64.b64encode(Path(f["path"]).read_bytes()).decode()
                images.append({"type": "image_url", "image_url": {"url": f"data:{f['mime']};base64,{b64}"}})
            else:
                text += f"\n\n[Image '{f['filename']}' attached, but the selected model cannot view images.]"
        if images:
            msgs.append({"role": m["role"], "content": [{"type": "text", "text": text}, *images]})
        else:
            msgs.append({"role": m["role"], "content": text})
    return msgs


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/conversations/{cid}/messages")
async def send_message(cid: str, body: SendMessage):
    model = MODELS.get(body.model)
    if not model:
        raise HTTPException(400, f"Unknown model: {body.model}")
    now = time.time()
    with _db_lock, db() as c:
        conv = c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        if body.regenerate:
            last = c.execute("SELECT id, role FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
                             (cid,)).fetchone()
            if last and last["role"] == "assistant":
                c.execute("DELETE FROM messages WHERE id=?", (last["id"],))
        else:
            c.execute(
                "INSERT INTO messages (conversation_id, role, content, attachments, model, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (cid, "user", body.content, json.dumps(body.attachments), body.model, now),
            )
            if conv["title"] == "New chat":
                title = " ".join(body.content.split())[:60] or (
                    "File chat" if body.attachments else "New chat")
                c.execute("UPDATE conversations SET title=? WHERE id=?", (title, cid))
        c.execute("UPDATE conversations SET model=?, updated_at=? WHERE id=?", (body.model, now, cid))
        upstream_msgs = build_upstream_messages(c, cid, "image" in model["input_modalities"])

    payload = {"model": body.model, "messages": upstream_msgs, "stream": True}
    headers = {"Authorization": f"Bearer {model['key']}"}
    url = f"{CONFIG['api_base_url']}/v1/chat/completions"

    async def stream():
        answer: list[str] = []
        reasoning: list[str] = []
        error: str | None = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=600)) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode("utf-8", "ignore")[:500]
                        hint = {403: " (this key has no Invoke grant for the model)",
                                404: " (model not found or not ready)"}.get(r.status_code, "")
                        error = f"Gateway error {r.status_code}{hint}: {detail}"
                    else:
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            for choice in chunk.get("choices") or []:
                                delta = choice.get("delta") or {}
                                if delta.get("reasoning_content") or delta.get("reasoning"):
                                    piece = delta.get("reasoning_content") or delta.get("reasoning")
                                    reasoning.append(piece)
                                    yield sse("reasoning", piece)
                                if delta.get("content"):
                                    answer.append(delta["content"])
                                    yield sse("delta", delta["content"])
        except httpx.HTTPError as e:
            error = f"Connection problem: {e}"
        finally:
            # Save whatever we got, even if the user pressed Stop mid-answer.
            text = "".join(answer)
            if text:
                with _db_lock, db() as c:
                    c.execute(
                        "INSERT INTO messages (conversation_id, role, content, attachments, model, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (cid, "assistant", text, "[]", body.model, time.time()),
                    )
                    c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))
        if error:
            yield sse("error", error)
        yield sse("done", {})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
def health():
    return {"ok": True, "app": "lehmus-chat"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
