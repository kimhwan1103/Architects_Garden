from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
import json, uuid
import os

from dotenv import load_dotenv
ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

#langchain 관련 임포트
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from  langchain_google_genai import ChatGoogleGenerativeAI

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

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

class NoteIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = ""
    tags: List[str] = []

class NoteOut(NoteIn):
    id: str
    created_at: str
    updated_at: str

class Task(BaseModel):
    id: str
    title: str
    details: Optional[str] = ""
    depends_on: List[str] = []
    estimate_hours: Optional[float] = None

class Goal(BaseModel):
    id: str
    title: str
    rationale: Optional[str] = ""
    tasks: List[Task] = Field(default_factory=list)

class AnalyzeIn(BaseModel):
    title: str
    content: str

class AnalyzeOut(BaseModel):
    summary: str
    goals: List[Goal]
    mermaid: str

def _get_llm():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY 환경변수를 설정하세요")
    
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.2,
        max_output_tokens=None,
        convert_system_message_to_prompt=True,
    )

ANALYZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are an expert project analyst. Given a free-form note (KR/EN), extract:\n"
     "1) concise summary (<=120 chars)\n"
     "2) 3-6 intermediate goals\n"
     "3) 2-6 actionable tasks per goal (imperative, <=80 chars)\n"
     "4) Output note content for korean."
     "Return STRICT JSON ONLY with schema:\n"
     "```json\n"
     "{{\n"
     '  "summary": "string",\n'
     '  "goals": [\n'
     "    {{\n"
     '      "id": "G1",\n'
     '      "title": "string",\n'
     '      "rationale": "string",\n'
     '      "tasks": [\n'
     '        {{"id":"T1","title":"string","details":"string","depends_on":[],"estimate_hours":1.5}}\n'
     "      ]\n"
     "    }}\n"
     "  ]\n"
     "}}\n"
     "```\n"
     "Use IDs like G1,G2 and T1,T2. No commentary."
    ),
    ("user",
     "Title: {title}\n"
     "Note:\n{content}\n"
     "Output JSON ONLY.")
])

_parser = JsonOutputParser()

#마인드맵 생성
def _to_mermaid(goals: List[Goal]) -> str:
    lines = ["graph TD", '  ROOT["Note Analysis"]:::root']
    styles = [
        "classDef root fill:#eef,stroke:#669,stroke-width:1px,color:#000",
        "classDef goal fill:#efe,stroke:#393,stroke-width:1px,color:#000",
        "classDef task fill:#ffe,stroke:#aa0,stroke-width:1px,color:#000",
    ]
    used_ids = set()
    used_tasks = set()

    for g in goals:
        gid = g.id or f"G{len(used_ids)+1}"
        if gid in used_ids:
            gid = f"{gid}_{len(used_ids)+1}"
        used_ids.add(gid)
        title = (g.title or "").replace('"', "'")
        lines.append(f'  ROOT --> {gid}["{title}"]:::goal')

        for t in g.tasks:
            tid = t.id or f"T{len(used_tasks)+1}"
            if tid in used_tasks:
                tid = f"{tid}_{len(used_tasks)+1}"
            used_tasks.add(tid)
            ttitle = (t.title or "").replace('"', "'")
            lines.append(f'  {gid} --> {tid}["{ttitle}"]:::task')
            for dep in t.depends_on or []:
                lines.append(f'  {dep} --> {tid}')

    lines.extend(styles)
    return "\n".join(lines)

@app.post("/api/analyze", response_model=AnalyzeOut)
def analyze_note(payload: AnalyzeIn):
    try:
        llm = _get_llm()
        chain = ANALYZE_PROMPT | llm | _parser

        raw = chain.invoke({"title": payload.title, "content": payload.content})
        goals: List[Goal] = []
        for g in raw.get("goals", []):
            tasks = [Task(**t) for t in g.get("tasks", [])]
            goals.append(Goal(
                id=str(g.get("id") or ""),
                title=str(g.get("title") or ""),
                rationale=str(g.get("rationale") or ""),
                tasks=tasks
            ))
        summary = str(raw.get("summary") or "")[:120]
        mermaid = _to_mermaid(goals)
        return AnalyzeOut(summary=summary, goals=goals, mermaid=mermaid)
    except Exception as e:
        return AnalyzeOut(
            summary="분석 실패: " + str(e)[:100],
            goals=[],
            mermaid="graph TD\n ROOT[Error]\n"
        )

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
