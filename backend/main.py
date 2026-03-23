import os
import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

# ─── Database ─────────────────────────────────────────────────────────────────

class Feedback(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    article_url: str = Field(index=True)
    article_title: str
    source: str
    category: str
    liked: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./digest.db")
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL)
SQLModel.metadata.create_all(engine)

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Morning Digest")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FEEDS = [
    {"name": "Quanta – Physics",  "url": "https://www.quantamagazine.org/physics/feed/",                  "category": "Physics"},
    {"name": "APS Physics",       "url": "https://feeds.aps.org/rss/recent/physics.rss",                  "category": "Physics"},
    {"name": "Backreaction",      "url": "https://backreaction.blogspot.com/feeds/posts/default?alt=rss", "category": "Physics"},
    {"name": "Simon Willison",    "url": "https://simonwillison.net/atom/everything/",                    "category": "AI"},
    {"name": "The Batch",         "url": "https://www.deeplearning.ai/the-batch/feed/",                   "category": "AI"},
    {"name": "Canary Media",      "url": "https://www.canarymedia.com/articles/feed.rss",                 "category": "Energy"},
    {"name": "Carbon Brief",      "url": "https://www.carbonbrief.org/feed/",                             "category": "Energy"},
    {"name": "Aeon",              "url": "https://aeon.co/feed.rss",                                      "category": "Philosophy"},
    {"name": "Astral Codex Ten",  "url": "https://www.astralcodexten.com/feed",                           "category": "Philosophy"},
]

# ─── RSS parsing ──────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    return " ".join(text.split()).strip()

def parse_feed(xml_text: str, meta: dict) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    is_atom = "}" in root.tag and "feed" in root.tag.lower()
    items = []

    if is_atom:
        ns = "http://www.w3.org/2005/Atom"
        for entry in root.findall(f"{{{ns}}}entry")[:7]:
            def ag(tag):
                el = entry.find(f"{{{ns}}}{tag}")
                return (el.text or "").strip() if el is not None else ""
            link_el = (entry.find(f"{{{ns}}}link[@rel='alternate']")
                       or entry.find(f"{{{ns}}}link"))
            link = link_el.get("href", "") if link_el is not None else ""
            summary = ag("summary") or ag("content")
            items.append({
                "title": strip_html(ag("title")),
                "description": strip_html(summary)[:350],
                "link": link,
                "pubDate": ag("published") or ag("updated"),
                "source": meta["name"],
                "category": meta["category"],
            })
    else:
        channel = root.find("channel")
        if channel is None:
            return []
        CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
        for item in channel.findall("item")[:7]:
            def rg(tag):
                el = item.find(tag)
                return (el.text or "").strip() if el is not None else ""
            desc = rg(f"{{{CONTENT_NS}}}encoded") or rg("description")
            items.append({
                "title": strip_html(rg("title")),
                "description": strip_html(desc)[:350],
                "link": rg("link") or rg("guid"),
                "pubDate": rg("pubDate"),
                "source": meta["name"],
                "category": meta["category"],
            })

    return [i for i in items if i["title"] and i["link"]]

async def fetch_feed(client: httpx.AsyncClient, feed: dict) -> list[dict]:
    try:
        r = await client.get(
            feed["url"], timeout=10, follow_redirects=True,
            headers={"User-Agent": "MorningDigest/1.0 RSS Reader"}
        )
        if r.status_code == 200:
            return parse_feed(r.text, feed)
    except Exception:
        pass
    return []

# ─── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/articles")
async def get_articles():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[fetch_feed(client, f) for f in FEEDS])
    articles, feed_status = [], {}
    for i, items in enumerate(results):
        name = FEEDS[i]["name"]
        feed_status[name] = "ok" if items else "fail"
        articles.extend(items)
    return {"articles": articles, "feed_status": feed_status, "total": len(articles)}

@app.get("/api/feedback/recent")
def get_recent_feedback(limit: int = 30):
    half = limit // 2
    with Session(engine) as session:
        liked = session.exec(
            select(Feedback).where(Feedback.liked == True)
            .order_by(Feedback.created_at.desc()).limit(half)
        ).all()
        disliked = session.exec(
            select(Feedback).where(Feedback.liked == False)
            .order_by(Feedback.created_at.desc()).limit(half)
        ).all()
    return {
        "liked":    [{"title": f.article_title, "source": f.source, "category": f.category} for f in liked],
        "disliked": [{"title": f.article_title, "source": f.source, "category": f.category} for f in disliked],
    }

class FeedbackIn(BaseModel):
    article_url: str
    article_title: str
    source: str
    category: str
    liked: bool

@app.post("/api/feedback")
def submit_feedback(body: FeedbackIn):
    with Session(engine) as session:
        existing = session.exec(
            select(Feedback).where(Feedback.article_url == body.article_url)
        ).first()
        if existing:
            existing.liked = body.liked
            session.add(existing)
        else:
            session.add(Feedback(**body.model_dump()))
        session.commit()
    return {"ok": True}

# ─── Frontend ─────────────────────────────────────────────────────────────────

FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/manifest.json")
def manifest():
    return FileResponse(os.path.join(FRONTEND_DIR, "manifest.json"))

@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

@app.get("/")
def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
