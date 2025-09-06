from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
import json, uuid

ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data" / "notes"
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local Notes API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

class NoteIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = ""
    tags: List[str] = []

class NoteOut(NoteIn):
    id: str
    created_at: str
    updated_at: str

def _p(note_id: str) -> Path: return DATA_DIR / f"{note_id}.json"

def _load(note_id: str) -> dict:
    p = _p(note_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    with p.open(encoding="utf-8") as f:
        return json.load(f)
    
def _save(obj: dict):
    with _p(obj["id"]).open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

@app.post("/api/notes", response_model=NoteOut)
def create_note(note: NoteIn):
    nid = uuid.uuid4().hex
    payload = {
        "id": nid,
        "title": note.title,
        "content": note.content,
        "tags": note.tags,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    _save(payload)
    return payload

@app.get("/api/notes", response_model=List[NoteOut])
def list_notes(query: Optional[str] = None, tag: Optional[str] = None):
    items = []
    for fp in DATA_DIR.glob("*.json"):
        with fp.open(encoding="utf-8") as f:
            #items.append(json.load(f))
            item = json.load(f)
            if "updated_at" not in item:
                item["updated_at"] = item.get("created_at", datetime.now(timezone.utc).isoformat())
            items.append(item)

    #간단 검색/필터
    if query:
        q = query.lower()
        items = [x for x in items if q in x["title"].lower() or q in x["content"].lower()]
    if tag:
        items = [x for x in items if tag in x.get("tags", [])]
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items

@app.get("/api/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: str):
    return _load(note_id)

@app.put("/api/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: str, note: NoteIn):
    obj = _load(note_id)
    obj["title"] = note.title
    obj["content"] = note.content
    obj["tags"] = note.tags
    obj["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(obj)
    return obj

@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    p = _p(note_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    p.unlink()
    return {"ok": True}

class ChatIn(BaseModel):
    message: str
    history: List[dict] = []

class ChatOut(BaseModel):
    reply: str

@app.post("/api/chat", response_model=ChatOut)
def chat(chat: ChatIn):
    user = chat.message.strip()
    if not user:
        return {"reply" : "무엇을 도와드릴까요?"}
    if "요약" in user or "summar" in user.lower():
         return {"reply": "요약이 필요하군요. 가운데 편집창의 내용을 복사해 붙여주시면 요약해드릴게요."}
    return {"reply": f"입력하신 내용 확인: “{user}”. 이 내용을 바탕으로 아이디어를 더 발전시켜 드릴까요?"}
