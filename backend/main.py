import os
import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import json

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
    {"name": "Quanta – Physics",       "url": "https://www.quantamagazine.org/physics/feed/",                    "category": "Physics"},
    {"name": "Matt Rickard",             "url": "https://matt-rickard.com/feed",                                   "category": "AI"},
    {"name": "Axios AI+",                "url": "https://axios.com/feeds/feed.rss",                               "category": "AI"},
    {"name": "Pragmatic Engineer",      "url": "https://newsletter.pragmaticengineer.com/feed",                   "category": "AI"},
    {"name": "One Useful Thing",        "url": "https://www.oneusefulthing.org/feed",                             "category": "AI"},
    {"name": "Simon Willison",          "url": "https://simonwillison.net/atom/everything/",                      "category": "AI"},
    {"name": "Gurwinder's The Prism",   "url": "https://gurwinder.substack.com/feed",                             "category": "Philosophy"},
    {"name": "Works in Progress",       "url": "https://worksinprogress.co/feed/",                                "category": "Philosophy"},
    {"name": "Heatmap News",            "url": "https://heatmap.news/feed",                                       "category": "Energy"},
    {"name": "Carbon Brief",            "url": "https://www.carbonbrief.org/feed/",                               "category": "Energy"},
    {"name": "iJustVibeCoded",          "url": "https://kill-the-newsletter.com/feeds/rychfe075y3dsl5zjm9c.xml",  "category": "AI"},
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

# ─── Ranking ─────────────────────────────────────────────────────────────────

class RankIn(BaseModel):
    articles: list[dict]
    feedback: dict

@app.post("/api/rank")
async def rank_articles(body: RankIn):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=500)

    articles = body.articles
    feedback_ctx = body.feedback

    article_list = "\n\n".join(
        f"[{i}] SOURCE: {a.get('source','')} | CATEGORY: {a.get('category','')}\n"
        f"TITLE: {a.get('title','')}\nSNIPPET: {a.get('description','')}"
        for i, a in enumerate(articles)
    )

    taste_context = ""
    liked = feedback_ctx.get("liked", [])
    disliked = feedback_ctx.get("disliked", [])
    if liked or disliked:
        taste_context = "\n\nMatt's reading history (use this to calibrate your picks):"
        if liked:
            taste_context += "\nARTICLES HE LIKED:\n" + "\n".join(
                f'- "{f.get("title","")}" ({f.get("source","")}, {f.get("category","")})'
                for f in liked
            )
        if disliked:
            taste_context += "\nARTICLES HE DIDN'T LIKE:\n" + "\n".join(
                f'- "{f.get("title","")}" ({f.get("source","")}, {f.get("category","")})'
                for f in disliked
            )
        taste_context += "\nUse this taste signal to weight your selections — lean toward what he's liked, avoid patterns he's disliked."

    prompt = (
        "You are a brilliant editorial curator helping Matt — a GTM strategy professional "
        "in Vancouver, intellectually curious across physics, AI productivity, clean energy, "
        f"and philosophy — pick his best morning reads.{taste_context}\n\n"
        "From the articles below, select the 6 most intellectually interesting, surprising, "
        "or substantive ones. Prioritize genuine insight over routine news, counterintuitive "
        "findings, and depth.\n\n"
        "Return a JSON array of exactly 6 objects:\n"
        "- index: integer (the [N] number)\n"
        "- headline: punchy 8–12 word rewrite capturing WHY it's interesting\n"
        "- hook: one sentence max 25 words — what makes this worth reading this morning\n"
        "- readTime: estimated minutes (integer, 2–8)\n\n"
        "Return ONLY valid JSON. No preamble, no markdown.\n\n"
        f"ARTICLES:\n{article_list}"
    )

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )

    if r.status_code != 200:
        return JSONResponse({"error": "Anthropic API error", "detail": r.text}, status_code=502)

    data = r.json()
    text = data.get("content", [{}])[0].get("text", "[]")
    try:
        ranked = json.loads(text.replace("```json", "").replace("```", "").strip())
        picks = [{**p, **articles[p["index"]]} for p in ranked if p.get("index") is not None and p["index"] < len(articles)]
        picks = [p for p in picks if p.get("title")]
    except (json.JSONDecodeError, KeyError, IndexError):
        picks = []

    return {"picks": picks}

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
