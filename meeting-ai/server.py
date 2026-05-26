import json
import os
import time
import uuid
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/meeting/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            d = json.load(open(DATA_FILE))
            return d.get("trips", {}), d.get("rooms", {})
        except Exception:
            pass
    return {}, {}

def save_data():
    rooms_serializable = {}
    for rid, r in rooms.items():
        rc = dict(r)
        rc["votes"] = {k: list(v) for k, v in r["votes"].items()}
        rooms_serializable[rid] = rc
    with open(DATA_FILE, "w") as f:
        json.dump({"trips": trips, "rooms": rooms_serializable}, f, ensure_ascii=False)

trips, rooms = load_data()
# Restore votes as sets
for r in rooms.values():
    r["votes"] = {k: set(v) for k, v in r.get("votes", {}).items()}

CLAUDE_API_KEY = "sk-acw-ab368e23-755e2f50282649ed"
CLAUDE_BASE_URL = "https://api.aicodewith.com"
CLAUDE_MODEL = "claude-opus-4-7"

DEFAULT_PROMPT = (
    "请从以下讨论发言中，提炼出三个维度的精华内容：\n\n"
    "## 发言总结\n"
    "用3-5句话概括本次讨论的核心内容和整体方向。\n\n"
    "## 最具新颖性的观点\n"
    "选出最有创意、最出人意料、最能打开新视角的观点或想法（通常3-5条）。每条注明发言者姓名，并用一句话说明为什么新颖。\n\n"
    "## 最值得深入的问题\n"
    "选出最有深度、最能引发思考、最值得继续探讨的问题（通常3-5条）。每条注明发言者姓名，并用一句话说明为什么这个问题好。\n\n"
    "输出格式清晰，用中文，语气简洁有力。"
)

RANK_PROMPT = (
    "请从以下会议发言中，选出所有质量较高、值得关注的发言（可以是问题、观点或建议）。\n"
    "评选标准：思考深度、创新性、实用性、启发性。\n"
    "数量不限，选出所有达到较高水准的发言，通常3-8条。\n\n"
    '请以JSON格式返回：{"top5": [{"id": "消息id", "reason": "入选理由（一句话）"}, ...]}\n\n'
    "只返回JSON，不要其他内容。"
)


class ConnectionManager:
    def __init__(self):
        self.active: dict = {}

    async def connect(self, room_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(room_id, []).append(ws)

    def disconnect(self, room_id: str, ws: WebSocket):
        if room_id in self.active:
            try:
                self.active[room_id].remove(ws)
            except ValueError:
                pass

    async def broadcast(self, room_id: str, data: dict):
        dead = []
        for ws in self.active.get(room_id, []):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room_id, ws)


manager = ConnectionManager()


class CreateTripReq(BaseModel):
    name: str
    admin_password: str


class CreateRoomReq(BaseModel):
    name: str
    admin_password: str


class PostMsgReq(BaseModel):
    author: str
    content: str


class RecallMsgReq(BaseModel):
    author: str


class UpdatePromptReq(BaseModel):
    admin_password: str
    prompt: str | None = None


class VoteReq(BaseModel):
    voter: str
    message_id: str


@app.post("/meeting/api/trips")
async def create_trip(req: CreateTripReq):
    trip_id = str(uuid.uuid4())[:8]
    trips[trip_id] = {
        "id": trip_id,
        "name": req.name,
        "admin_password": req.admin_password,
        "ai_prompt": DEFAULT_PROMPT,
        "schedule": [],
        "topics_enabled": False,
        "propose_open": True,
        "current_round": 1,
        "topics": [],
        "mentors_enabled": False,
        "mentors": [],
        "rooms": [],
        "created_at": time.time(),
    }
    save_data()
    return {"trip_id": trip_id}


def _normalize_schedule(s):
    if isinstance(s, list):
        return s
    if isinstance(s, str) and s.strip():
        return [{"date": "", "content": s}]
    return []


@app.get("/meeting/api/trips/{trip_id}")
async def get_trip(trip_id: str):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    room_list = []
    for rid in trip["rooms"]:
        r = rooms.get(rid)
        if r:
            room_list.append({"id": r["id"], "name": r["name"], "msg_count": len(r["messages"])})
    return {
        "id": trip["id"],
        "name": trip["name"],
        "schedule": _normalize_schedule(trip.get("schedule")),
        "topics_enabled": trip.get("topics_enabled", False),
        "propose_open": trip.get("propose_open", True),
        "current_round": trip.get("current_round", 1),
        "topics": trip.get("topics", []),
        "mentors_enabled": trip.get("mentors_enabled", False),
        "mentors": _public_mentors(trip),
        "rooms": room_list,
    }


RATING_KEYS = ["content", "depth", "case", "delivery", "interaction", "overall", "again"]


def _avg(nums):
    nums = [n for n in nums if isinstance(n, (int, float))]
    return round(sum(nums) / len(nums), 2) if nums else None


def _public_mentors(trip):
    mentors = trip.get("mentors", []) or []
    out = []
    for m in mentors:
        ratings = m.get("ratings", []) or []
        avgs = {k: _avg([r.get("scores", {}).get(k) for r in ratings]) for k in RATING_KEYS}
        comments = [r.get("comment", "").strip() for r in ratings if r.get("comment", "").strip()]
        out.append({
            "id": m["id"],
            "name": m["name"],
            "bio": m.get("bio", ""),
            "avatar": m.get("avatar", ""),
            "count": len(ratings),
            "avgs": avgs,
            "comments": comments,
        })
    return out


class ScheduleItem(BaseModel):
    date: str = ""
    content: str = ""


class UpdateScheduleReq(BaseModel):
    admin_password: str
    schedule: list[ScheduleItem]


@app.post("/meeting/api/trips/{trip_id}/schedule")
async def update_schedule(trip_id: str, req: UpdateScheduleReq):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["schedule"] = [item.model_dump() for item in req.schedule]
    save_data()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
# 同学夜谈：选题 + 报名
# ══════════════════════════════════════════════════════════

TOPIC_SIGNUP_LIMIT = 6


class ProposeTopicReq(BaseModel):
    proposer: str
    company: str = ""
    industry: str = ""
    title: str
    desc: str = ""


class SignupTopicReq(BaseModel):
    user_name: str
    industry: str = ""
    company: str = ""


class TopicsEnableReq(BaseModel):
    admin_password: str
    enabled: bool


class NewRoundReq(BaseModel):
    admin_password: str


class TopicAdminReq(BaseModel):
    admin_password: str
    topic_id: str


def _get_trip_or_404(trip_id):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    return trip


def _find_topic(trip, topic_id):
    return next((t for t in trip.get("topics", []) if t["id"] == topic_id), None)


@app.post("/meeting/api/trips/{trip_id}/topics/enable")
async def set_topics_enabled(trip_id: str, req: TopicsEnableReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["topics_enabled"] = req.enabled
    if "current_round" not in trip:
        trip["current_round"] = 1
    if "topics" not in trip:
        trip["topics"] = []
    save_data()
    return {"ok": True, "topics_enabled": trip["topics_enabled"]}


@app.post("/meeting/api/trips/{trip_id}/topics/new_round")
async def new_round(trip_id: str, req: NewRoundReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["current_round"] = trip.get("current_round", 1) + 1
    trip["propose_open"] = True
    save_data()
    return {"ok": True, "current_round": trip["current_round"]}


class ProposeOpenReq(BaseModel):
    admin_password: str
    open: bool


@app.post("/meeting/api/trips/{trip_id}/topics/propose_open")
async def set_propose_open(trip_id: str, req: ProposeOpenReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["propose_open"] = req.open
    save_data()
    return {"ok": True, "propose_open": trip["propose_open"]}


@app.post("/meeting/api/trips/{trip_id}/topics")
async def propose_topic(trip_id: str, req: ProposeTopicReq):
    trip = _get_trip_or_404(trip_id)
    if not trip.get("topics_enabled"):
        raise HTTPException(400, "同学夜谈未开启")
    if not trip.get("propose_open", True):
        raise HTTPException(400, "提报已关闭")
    proposer = req.proposer.strip()
    title = req.title.strip()
    if not proposer or not title:
        raise HTTPException(400, "姓名和标题不能为空")
    current_round = trip.get("current_round", 1)
    topics = trip.setdefault("topics", [])
    # 同轮内，同一人只能提报一次
    if any(t["proposer"] == proposer and t["round"] == current_round and not t.get("cancelled") for t in topics):
        raise HTTPException(400, "你本轮已经提报过话题了")
    # 同轮内，已报名别的话题不能再提报
    if any(proposer in t.get("signups", []) for t in topics if t["round"] == current_round and not t.get("cancelled")):
        raise HTTPException(400, "你本轮已报名其他话题，不能再提报")
    topic_id = str(uuid.uuid4())[:8]
    topics.append({
        "id": topic_id,
        "round": current_round,
        "proposer": proposer,
        "company": req.company.strip(),
        "industry": req.industry.strip(),
        "title": title,
        "desc": req.desc.strip(),
        "signups": [],
        "full": False,
        "cancelled": False,
        "created_at": time.time(),
    })
    save_data()
    return {"ok": True, "topic_id": topic_id}


@app.post("/meeting/api/trips/{trip_id}/topics/{topic_id}/signup")
async def signup_topic(trip_id: str, topic_id: str, req: SignupTopicReq):
    trip = _get_trip_or_404(trip_id)
    topic = _find_topic(trip, topic_id)
    if not topic or topic.get("cancelled"):
        raise HTTPException(404, "话题不存在或已取消")
    if topic.get("full"):
        raise HTTPException(400, "该话题已满")
    user = req.user_name.strip()
    if not user:
        raise HTTPException(400, "请先填写姓名")
    industry = req.industry.strip()
    company = req.company.strip()
    current_round = trip.get("current_round", 1)
    # 分享者不能报别人的话题（同轮内）
    if topic["round"] == current_round:
        for t in trip.get("topics", []):
            if t["round"] == current_round and not t.get("cancelled") and t["proposer"] == user:
                raise HTTPException(400, "你本轮已提报话题，不能再报名其他话题")
    signups = topic.setdefault("signups", [])
    if any((s["name"] if isinstance(s, dict) else s) == user for s in signups):
        return {"ok": True, "already": True}
    # 本轮内只能报一个，自动取消之前的
    if topic["round"] == current_round:
        for t in trip.get("topics", []):
            if t["round"] == current_round and t["id"] != topic_id:
                t["signups"] = [s for s in t.get("signups", []) if (s["name"] if isinstance(s, dict) else s) != user]
                if t.get("full") and len(t["signups"]) < TOPIC_SIGNUP_LIMIT:
                    t["full"] = False
    signups.append({"name": user, "industry": industry, "company": company})
    if len(signups) >= TOPIC_SIGNUP_LIMIT:
        topic["full"] = True
    save_data()
    return {"ok": True, "count": len(signups), "full": topic.get("full", False)}


@app.post("/meeting/api/trips/{trip_id}/topics/{topic_id}/unsignup")
async def unsignup_topic(trip_id: str, topic_id: str, req: SignupTopicReq):
    trip = _get_trip_or_404(trip_id)
    topic = _find_topic(trip, topic_id)
    if not topic:
        raise HTTPException(404, "话题不存在")
    user = req.user_name.strip()
    signups = topic.get("signups", [])
    new_signups = [s for s in signups if (s["name"] if isinstance(s, dict) else s) != user]
    if len(new_signups) != len(signups):
        topic["signups"] = new_signups
        if len(new_signups) < TOPIC_SIGNUP_LIMIT:
            topic["full"] = False
        save_data()
    return {"ok": True}


@app.post("/meeting/api/trips/{trip_id}/topics/{topic_id}/toggle_full")
async def toggle_full(trip_id: str, topic_id: str, req: TopicAdminReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    topic = _find_topic(trip, topic_id)
    if not topic:
        raise HTTPException(404, "话题不存在")
    topic["full"] = not topic.get("full", False)
    save_data()
    return {"ok": True, "full": topic["full"]}


@app.post("/meeting/api/trips/{trip_id}/topics/{topic_id}/cancel")
async def cancel_topic(trip_id: str, topic_id: str, req: TopicAdminReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    topic = _find_topic(trip, topic_id)
    if not topic:
        raise HTTPException(404, "话题不存在")
    topic["cancelled"] = True
    save_data()
    return {"ok": True}


@app.post("/meeting/api/trips/{trip_id}/topics/{topic_id}/delete")
async def delete_topic(trip_id: str, topic_id: str, req: TopicAdminReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    topics = trip.get("topics", [])
    new_topics = [t for t in topics if t["id"] != topic_id]
    if len(new_topics) == len(topics):
        raise HTTPException(404, "话题不存在")
    trip["topics"] = new_topics
    save_data()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
# 导师评分
# ══════════════════════════════════════════════════════════


class ReorderMentorsReq(BaseModel):
    admin_password: str
    order: list[str]


@app.post("/meeting/api/trips/{trip_id}/mentors/reorder")
async def reorder_mentors(trip_id: str, req: ReorderMentorsReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    mentors = trip.get("mentors", [])
    by_id = {m["id"]: m for m in mentors}
    new_list = [by_id[i] for i in req.order if i in by_id]
    # 兜底：未在 order 里的导师追加到末尾
    seen = set(req.order)
    for m in mentors:
        if m["id"] not in seen:
            new_list.append(m)
    trip["mentors"] = new_list
    save_data()
    return {"ok": True}


class MentorsEnableReq(BaseModel):
    admin_password: str
    enabled: bool


class AddMentorReq(BaseModel):
    admin_password: str
    name: str
    bio: str = ""


class DeleteMentorReq(BaseModel):
    admin_password: str


class RatingScores(BaseModel):
    content: int
    depth: int
    case: int
    delivery: int
    interaction: int
    overall: int
    again: int


class SubmitRatingReq(BaseModel):
    rater: str
    scores: RatingScores
    comment: str = ""


def _find_mentor(trip, mentor_id):
    return next((m for m in trip.get("mentors", []) if m["id"] == mentor_id), None)


@app.post("/meeting/api/trips/{trip_id}/mentors/enable")
async def set_mentors_enabled(trip_id: str, req: MentorsEnableReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["mentors_enabled"] = req.enabled
    if "mentors" not in trip:
        trip["mentors"] = []
    save_data()
    return {"ok": True, "mentors_enabled": trip["mentors_enabled"]}


@app.post("/meeting/api/trips/{trip_id}/mentors")
async def add_mentor(trip_id: str, req: AddMentorReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "导师姓名不能为空")
    mentor = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "bio": req.bio.strip(),
        "avatar": "",
        "ratings": [],
        "created_at": time.time(),
    }
    trip.setdefault("mentors", []).append(mentor)
    save_data()
    return {"ok": True, "mentor_id": mentor["id"]}


@app.post("/meeting/api/trips/{trip_id}/mentors/{mentor_id}/avatar")
async def upload_mentor_avatar(trip_id: str, mentor_id: str, admin_password: str = Form(...), file: UploadFile = File(...)):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    mentor = _find_mentor(trip, mentor_id)
    if not mentor:
        raise HTTPException(404, "导师不存在")
    ctype = (file.content_type or "").lower()
    if not ctype.startswith("image/"):
        raise HTTPException(400, "请上传图片文件")
    ext = ".jpg"
    if "png" in ctype: ext = ".png"
    elif "webp" in ctype: ext = ".webp"
    elif "gif" in ctype: ext = ".gif"
    fname = f"mentor_{mentor_id}_{int(time.time())}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 50MB")
    with open(fpath, "wb") as f:
        f.write(content)
    # 删旧文件
    old = mentor.get("avatar") or ""
    if old.startswith("/meeting/uploads/"):
        old_path = os.path.join(UPLOAD_DIR, os.path.basename(old))
        if os.path.exists(old_path) and os.path.abspath(old_path).startswith(os.path.abspath(UPLOAD_DIR)):
            try: os.remove(old_path)
            except OSError: pass
    mentor["avatar"] = f"/meeting/uploads/{fname}"
    save_data()
    return {"ok": True, "avatar": mentor["avatar"]}



async def delete_mentor(trip_id: str, mentor_id: str, req: DeleteMentorReq):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    mentors = trip.get("mentors", [])
    target = next((m for m in mentors if m["id"] == mentor_id), None)
    if not target:
        raise HTTPException(404, "导师不存在")
    avatar = target.get("avatar") or ""
    if avatar.startswith("/meeting/uploads/"):
        ap = os.path.join(UPLOAD_DIR, os.path.basename(avatar))
        if os.path.exists(ap) and os.path.abspath(ap).startswith(os.path.abspath(UPLOAD_DIR)):
            try: os.remove(ap)
            except OSError: pass
    trip["mentors"] = [m for m in mentors if m["id"] != mentor_id]
    save_data()
    return {"ok": True}


@app.post("/meeting/api/trips/{trip_id}/mentors/{mentor_id}/rate")
async def submit_rating(trip_id: str, mentor_id: str, req: SubmitRatingReq):
    trip = _get_trip_or_404(trip_id)
    if not trip.get("mentors_enabled"):
        raise HTTPException(400, "导师评分未开启")
    mentor = _find_mentor(trip, mentor_id)
    if not mentor:
        raise HTTPException(404, "导师不存在")
    rater = req.rater.strip()
    if not rater:
        raise HTTPException(400, "请先填写姓名")
    scores = req.scores.model_dump()
    for k, v in scores.items():
        if not (1 <= v <= 5):
            raise HTTPException(400, f"{k} 分数应在 1-5")
    ratings = mentor.setdefault("ratings", [])
    existing = next((r for r in ratings if r.get("rater") == rater), None)
    if existing:
        existing["scores"] = scores
        existing["comment"] = req.comment.strip()
        existing["ts"] = time.time()
    else:
        ratings.append({
            "rater": rater,
            "scores": scores,
            "comment": req.comment.strip(),
            "ts": time.time(),
        })
    save_data()
    return {"ok": True, "updated": existing is not None}


@app.get("/meeting/api/trips/{trip_id}/mentors/{mentor_id}/my_rating")
async def get_my_rating(trip_id: str, mentor_id: str, rater: str):
    trip = _get_trip_or_404(trip_id)
    mentor = _find_mentor(trip, mentor_id)
    if not mentor:
        raise HTTPException(404, "导师不存在")
    r = next((r for r in mentor.get("ratings", []) if r.get("rater") == rater.strip()), None)
    if not r:
        return {"rating": None}
    return {"rating": {"scores": r["scores"], "comment": r.get("comment", "")}}


@app.get("/meeting/api/trips/{trip_id}/mentors/admin")
async def admin_view_mentors(trip_id: str, admin_password: str):
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    out = []
    for m in trip.get("mentors", []):
        ratings = m.get("ratings", []) or []
        avgs = {k: _avg([r.get("scores", {}).get(k) for r in ratings]) for k in RATING_KEYS}
        # 评分匿名化：去掉 rater
        anon_ratings = [{
            "scores": r.get("scores", {}),
            "comment": r.get("comment", ""),
            "ts": r.get("ts"),
        } for r in ratings]
        out.append({
            "id": m["id"],
            "name": m["name"],
            "bio": m.get("bio", ""),
            "avatar": m.get("avatar", ""),
            "count": len(ratings),
            "avgs": avgs,
            "ratings": anon_ratings,
        })
    return {"mentors": out}


@app.get("/meeting/api/trips/{trip_id}/mentors/export")
async def export_mentor_ratings(trip_id: str, admin_password: str):
    from fastapi.responses import Response
    import csv, io
    trip = _get_trip_or_404(trip_id)
    if trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    dim_labels = {
        "content": "内容价值",
        "depth": "专业深度",
        "case": "案例信息量",
        "delivery": "表达节奏",
        "interaction": "互动回应",
        "overall": "综合评分",
        "again": "下次还想听",
    }
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["导师", "评分人"] + [dim_labels[k] for k in RATING_KEYS] + ["留言", "提交时间"])
    for m in trip.get("mentors", []):
        for r in m.get("ratings", []):
            scores = r.get("scores", {}) or {}
            ts = r.get("ts")
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
            w.writerow([
                m["name"],
                r.get("rater", ""),
                *[scores.get(k, "") for k in RATING_KEYS],
                r.get("comment", ""),
                ts_str,
            ])
    csv_text = "﻿" + buf.getvalue()
    fname = f"mentor_ratings_{trip_id}_{int(time.time())}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/meeting/api/trips/{trip_id}/rooms")
async def create_room(trip_id: str, req: CreateRoomReq):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    room_id = str(uuid.uuid4())[:8]
    rooms[room_id] = {
        "id": room_id,
        "trip_id": trip_id,
        "name": req.name,
        "ai_prompt": DEFAULT_PROMPT,
        "messages": [],
        "summary": None,
        "top5": [],
        "votes": {},
        "created_at": time.time(),
    }
    trip["rooms"].append(room_id)
    save_data()
    return {"room_id": room_id}


@app.get("/meeting/api/rooms/{room_id}")
async def get_room(room_id: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    return {
        "id": room["id"],
        "trip_id": room["trip_id"],
        "name": room["name"],
        "messages": room["messages"],
        "summary": room["summary"],
        "top5": room["top5"],
        "votes": {k: list(v) for k, v in room["votes"].items()},
    }


@app.post("/meeting/api/rooms/{room_id}/messages")
async def post_message(room_id: str, req: PostMsgReq):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    msg = {
        "id": str(uuid.uuid4())[:8],
        "author": req.author,
        "content": req.content,
        "timestamp": time.time(),
    }
    room["messages"].append(msg)
    save_data()
    await manager.broadcast(room_id, {"type": "new_message", "message": msg})
    return msg


@app.post("/meeting/api/rooms/{room_id}/messages/{message_id}/recall")
async def recall_message(room_id: str, message_id: str, req: RecallMsgReq):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    msg = next((m for m in room["messages"] if m["id"] == message_id), None)
    if not msg:
        raise HTTPException(404, "发言不存在")
    if msg["author"] != req.author:
        raise HTTPException(403, "只能撤回自己的发言")
    room["messages"].remove(msg)
    save_data()
    await manager.broadcast(room_id, {"type": "message_recalled", "message_id": message_id})
    return {"ok": True}


@app.post("/meeting/api/rooms/{room_id}/summarize")
async def summarize_room(room_id: str, admin_password: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    trip = trips.get(room["trip_id"])
    if not trip or trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    if not room["messages"]:
        raise HTTPException(400, "还没有发言内容")
    prompt = room.get("ai_prompt") or DEFAULT_PROMPT
    msgs_text = "\n".join(f"[{m['author']}]: {m['content']}" for m in room["messages"])
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{CLAUDE_BASE_URL}/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": CLAUDE_MODEL, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": f"{prompt}\n\n---讨论发言---\n{msgs_text}"}]},
        )
    if resp.status_code != 200:
        raise HTTPException(500, f"AI调用失败: {resp.text[:200]}")
    summary = resp.json()["content"][0]["text"]
    room["summary"] = summary
    save_data()
    await manager.broadcast(room_id, {"type": "summary", "summary": summary})
    return {"summary": summary}


@app.post("/meeting/api/rooms/{room_id}/rank")
async def rank_messages(room_id: str, admin_password: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    trip = trips.get(room["trip_id"])
    if not trip or trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    if len(room["messages"]) < 3:
        raise HTTPException(400, "发言太少，至少需要3条")
    msgs_text = "\n".join(f'id:{m["id"]} [{m["author"]}]: {m["content"]}' for m in room["messages"])
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{CLAUDE_BASE_URL}/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": CLAUDE_MODEL, "max_tokens": 1000,
                  "messages": [{"role": "user", "content": f"{RANK_PROMPT}\n\n---发言列表---\n{msgs_text}"}]},
        )
    if resp.status_code != 200:
        raise HTTPException(500, f"AI调用失败: {resp.text[:200]}")
    try:
        raw_text = resp.json()["content"][0]["text"]
        print("AI rank raw:", raw_text[:500])
        top5 = json.loads(raw_text).get("top5", [])
    except Exception as e:
        raw = resp.text[:300] if resp else "no response"
        print("AI rank error:", e, "raw:", raw)
        raise HTTPException(500, f"AI返回格式错误: {raw_text[:200] if 'raw_text' in dir() else raw}")
    # 把消息内容直接塞进 top5，客户端不用再查找
    msg_map = {m["id"]: m for m in room["messages"]}
    for item in top5:
        msg = msg_map.get(item["id"])
        if msg:
            item["author"] = msg["author"]
            item["content"] = msg["content"]
    room["top5"] = top5
    room["votes"] = {item["id"]: set() for item in top5}
    save_data()
    votes_out = {k: [] for k in room["votes"]}
    await manager.broadcast(room_id, {"type": "top5", "top5": top5, "votes": votes_out})
    return {"top5": top5}


@app.post("/meeting/api/rooms/{room_id}/vote")
async def vote_message(room_id: str, req: VoteReq):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "会议室不存在")
    if req.message_id not in room["votes"]:
        raise HTTPException(400, "该消息不在候选列表中")
    # 每人最多 3 票，同一条不能重复投
    voter_votes = sum(1 for voters in room["votes"].values() if req.voter in voters)
    already_voted_this = req.voter in room["votes"][req.message_id]
    if already_voted_this:
        # 取消这一票
        room["votes"][req.message_id].discard(req.voter)
    elif voter_votes >= 3:
        raise HTTPException(400, "每人最多投3票")
    else:
        room["votes"][req.message_id].add(req.voter)
    save_data()
    votes_out = {k: list(v) for k, v in room["votes"].items()}
    await manager.broadcast(room_id, {"type": "votes_update", "votes": votes_out})
    return {"ok": True}


@app.get("/meeting/api/rooms/{room_id}/prompt")
async def get_room_prompt(room_id: str, admin_password: str):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "议题不存在")
    trip = trips.get(room["trip_id"])
    if not trip or trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    return {"prompt": room.get("ai_prompt") or DEFAULT_PROMPT}


@app.post("/meeting/api/rooms/{room_id}/prompt")
async def update_room_prompt(room_id: str, req: UpdatePromptReq):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "议题不存在")
    trip = trips.get(room["trip_id"])
    if not trip or trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    room["ai_prompt"] = req.prompt if req.prompt else DEFAULT_PROMPT
    save_data()
    return {"ok": True}


@app.websocket("/meeting/ws/{room_id}")
async def ws_endpoint(websocket: WebSocket, room_id: str):
    await manager.connect(room_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)


HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>前哨游学 OS</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#f5f5f7;color:#1a1a1a;min-height:100vh;font-size:18px}
.header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#fff;padding:16px 20px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
.header h1{font-size:20px;font-weight:600}
.back-btn{background:none;border:none;color:#fff;font-size:22px;cursor:pointer;padding:4px 8px;border-radius:6px}
.back-btn:active{background:rgba(255,255,255,.15)}
.container{max-width:600px;margin:0 auto;padding:20px 16px}
.card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card h2{font-size:18px;font-weight:600;margin-bottom:14px;color:#1a1a1a}
input,textarea{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:18px;font-family:inherit;outline:none;transition:border-color .2s;background:#fafafa}
input:focus,textarea:focus{border-color:#007aff;background:#fff}
textarea{resize:vertical;min-height:80px}
.btn{display:block;width:100%;padding:15px;border:none;border-radius:10px;font-size:18px;font-weight:600;cursor:pointer;transition:opacity .15s,transform .1s}
.btn:active{opacity:.85;transform:scale(.98)}
.btn-primary{background:#007aff;color:#fff}
.btn-secondary{background:#f0f0f5;color:#1a1a1a}
.btn-danger{background:#ff3b30;color:#fff}
.btn-sm{padding:9px 18px;font-size:15px;width:auto;display:inline-block}
.btn-green{background:#34c759;color:#fff}
.label{font-size:15px;color:#666;margin-bottom:6px;font-weight:500}
.gap{margin-top:12px}
.room-card{background:#fff;border-radius:12px;padding:18px 16px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.07);display:flex;align-items:center;justify-content:space-between;cursor:pointer;transition:background .15s}
.room-card:active{background:#f0f0f5}
.room-name{font-size:18px;font-weight:600}
.room-meta{font-size:15px;color:#888;margin-top:2px}
.arrow{color:#c7c7cc;font-size:20px}
.msg-wall{background:#f5f5f7;border-radius:10px;padding:12px;min-height:200px;max-height:45dvh;overflow-y:auto;margin-bottom:12px}
.msg-item{background:#fff;border-radius:10px;padding:12px 14px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.msg-author{font-size:14px;color:#888;margin-bottom:4px}
.msg-content{font-size:18px;line-height:1.6;white-space:pre-wrap}
.msg-row{display:flex;gap:8px;align-items:flex-end}
.msg-input{flex:1}
.send-btn{background:#007aff;color:#fff;border:none;border-radius:10px;padding:12px 18px;font-size:18px;font-weight:600;cursor:pointer;white-space:nowrap}
.send-btn:active{opacity:.8}
.divider{height:1px;background:#e8e8ed;margin:16px 0}
.summary-box{background:#f0f7ff;border-radius:10px;padding:14px;font-size:16px;line-height:1.8;white-space:pre-wrap;color:#1a1a1a}
.top5-item{background:#fff;border-radius:10px;padding:14px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06);display:flex;gap:10px;align-items:flex-start}
.top5-rank{font-size:22px;min-width:28px}
.top5-body{flex:1}
.top5-content{font-size:16px;line-height:1.6;margin-bottom:4px}
.top5-reason{font-size:14px;color:#888}
.vote-btn{background:#f0f0f5;border:none;border-radius:8px;padding:8px 16px;font-size:15px;cursor:pointer;font-weight:600}
.vote-btn.voted{background:#007aff;color:#fff}
.vote-count{font-size:14px;color:#888;margin-left:6px}
.admin-panel{background:#fff8f0;border-radius:12px;padding:16px;margin-top:16px}
.admin-panel h3{font-size:15px;color:#ff9500;font-weight:600;margin-bottom:12px}
.admin-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tag{display:inline-block;background:#e8f4ff;color:#007aff;border-radius:6px;padding:3px 8px;font-size:13px;font-weight:600}
.toast{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#1a1a1a;color:#fff;padding:10px 20px;border-radius:20px;font-size:15px;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.loading{text-align:center;color:#888;padding:20px;font-size:14px}
.empty{text-align:center;color:#aaa;padding:30px;font-size:14px}
.qr-box{text-align:center;padding:10px 0}
.qr-box img{width:180px;height:180px;border-radius:10px}
.qr-url{font-size:12px;color:#888;margin-top:8px;word-break:break-all}
.admin-toggle{font-size:13px;color:#007aff;cursor:pointer;text-align:right;margin-top:8px}
.tabs{display:flex;background:#fff;border-radius:12px;padding:4px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.tab{flex:1;text-align:center;padding:12px;font-size:17px;font-weight:600;color:#888;cursor:pointer;border-radius:9px;transition:all .15s}
.tab.active{background:#007aff;color:#fff}
.schedule-text{font-size:17px;line-height:1.8;white-space:pre-wrap;color:#1a1a1a;min-height:120px}
.swipe-wrap{margin:0 -16px}
.swipe{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scroll-behavior:smooth;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.swipe::-webkit-scrollbar{display:none}
.swipe-page{flex:0 0 100%;scroll-snap-align:center;padding:0 16px;box-sizing:border-box}
.swipe-page .card{margin-bottom:0}
.dots{display:flex;justify-content:center;gap:8px;margin:12px 0 4px;flex-wrap:wrap}
.dot{font-size:13px;color:#888;background:#f0f0f5;border-radius:14px;padding:5px 11px;cursor:pointer;font-weight:600;transition:all .15s}
.dot.active{background:#007aff;color:#fff}
.dot.today{box-shadow:0 0 0 2px #34c759}
.day-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.day-title{font-size:18px;font-weight:600}
.day-date{font-size:14px;color:#888}
.topic-card{border:1.5px solid #e8e8ed;border-radius:12px;padding:14px;margin-bottom:10px;background:#fff;transition:all .15s}
.topic-card.cancelled{opacity:.5;border-color:#ffccc7}
.topic-card.full{background:#fafafa}
.topic-title{font-size:17px;font-weight:600;color:#1a1a1a;line-height:1.4}
.topic-meta{font-size:14px;color:#888;margin-top:4px}
.topic-desc{font-size:15px;color:#444;margin-top:8px;line-height:1.5;white-space:pre-wrap}
.topic-foot{display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:8px;flex-wrap:wrap}
.topic-count{font-size:14px;color:#007aff;font-weight:600}
.topic-tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:10px;background:#f0f0f5;color:#888;margin-left:6px}
.topic-tag.danger{background:#ffe5e5;color:#ff3b30}
.topic-tag.gray{background:#f0f0f5;color:#888}
.round-head{font-size:15px;font-weight:600;color:#888;margin:14px 0 8px;display:flex;align-items:center;gap:8px}
.round-head .round-tag{font-size:12px;background:#007aff;color:#fff;border-radius:10px;padding:2px 9px;font-weight:600}
.round-head .round-tag.past{background:#aaa}
.signup-names{font-size:13px;color:#888;margin-top:6px;line-height:1.6}
.signup-names span{display:inline-block;background:#f0f7ff;color:#007aff;border-radius:8px;padding:2px 8px;margin:1px 3px 1px 0}
.mentor-card{background:#fff;border:1.5px solid #e8e8ed;border-radius:12px;padding:16px;margin-bottom:10px}
.mentor-name{font-size:18px;font-weight:600;color:#1a1a1a}
.mentor-bio{font-size:14px;color:#888;margin-top:4px;line-height:1.5}
.mentor-stats{font-size:14px;color:#666;margin-top:10px;line-height:1.7}
.mentor-stats .num{color:#007aff;font-weight:700;font-size:16px}
.mentor-foot{display:flex;justify-content:space-between;align-items:center;margin-top:12px;gap:8px;flex-wrap:wrap}
.star-row{display:flex;align-items:center;gap:8px;margin:6px 0}
.star-label{flex:0 0 auto;font-size:15px;color:#1a1a1a;min-width:90px}
.star-btns{display:flex;gap:4px}
.star-btn{font-size:28px;cursor:pointer;line-height:1;color:#e0e0e0;user-select:none;-webkit-tap-highlight-color:transparent}
.star-btn.on{color:#ffb800}
.score-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid #f0f0f5}
.score-row:last-child{border-bottom:none}
.score-row .lbl{font-size:15px;color:#1a1a1a}
.score-row .lbl small{display:block;font-size:13px;color:#888;margin-top:2px;font-weight:normal}
.score-bar{display:flex;align-items:center;gap:8px}
.score-bar .num{font-weight:700;color:#007aff;min-width:36px;text-align:right;font-size:15px}
.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200;display:flex;align-items:flex-end;justify-content:center}
.modal-sheet{background:#fff;width:100%;max-width:600px;max-height:92vh;overflow-y:auto;border-radius:18px 18px 0 0;padding:18px 16px 24px}
.modal-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.modal-head h3{font-size:18px;font-weight:600}
.modal-close{background:none;border:none;font-size:26px;color:#888;cursor:pointer;padding:4px 8px}
.comment-list{margin-top:8px}
.comment-item{background:#fafafa;border-radius:10px;padding:10px 12px;margin-bottom:8px;font-size:15px;line-height:1.6;color:#333;white-space:pre-wrap}
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
const S={view:'home',tripId:null,roomId:null,userName:null,userIndustry:'',userCompany:'',adminPwd:null,tripData:null,roomData:null,tripTab:'schedule'};

function $(id){return document.getElementById(id)}
function app(){return $('app')}
function toast(msg,dur=2500){const t=$('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),dur)}

async function api(method,path,body){
  const opts={method,headers:{'Content-Type':'application/json'}};
  if(body)opts.body=JSON.stringify(body);
  const r=await fetch(path,opts);
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||'请求失败');
  return d;
}

function getAdminTrips(){
  try{return JSON.parse(localStorage.getItem('meeting_admin_trips')||'[]')}catch(e){return[]}
}

function saveAdminTrip(tripId,name,adminPwd){
  const list=getAdminTrips().filter(t=>t.tripId!==tripId);
  list.unshift({tripId,name,adminPwd});
  localStorage.setItem('meeting_admin_trips',JSON.stringify(list.slice(0,10)));
}

function renderHome(){
  const myTrips=getAdminTrips();
  const myTripsHtml=myTrips.length?`
  <div class="card"><h2>我的行程</h2>
    ${myTrips.map(t=>`<div class="room-card" onclick="reenterTrip('${t.tripId}','${t.adminPwd}')">
      <div><div class="room-name">${t.name}</div><div class="room-meta">${t.tripId}</div></div>
      <div class="arrow">›</div>
    </div>`).join('')}
  </div>`:'';
  app().innerHTML=`
<div class="header"><h1>前哨游学 OS</h1></div>
<div class="container">
  ${myTripsHtml}
  <div class="card">
    <h2>创建行程</h2>
    <div class="label">行程名称</div>
    <input id="tripName" placeholder="如：硅谷游学2026">
    <div style="font-size:12px;color:#888;margin-top:5px">行程名称将显示在外发二维码上，建议写清楚活动名称</div>
    <div class="gap"><div class="label">管理员密码</div>
    <input id="tripPwd" type="password" placeholder="设置密码，用于管理员操作"></div>
    <div class="gap"><button class="btn btn-primary" onclick="doCreate()">创建行程</button></div>
  </div>
</div>`;
}

async function reenterTrip(tripId,adminPwd){
  try{
    const td=await api('GET','/meeting/api/trips/'+tripId);
    S.tripId=tripId;S.adminPwd=adminPwd;S.userName='主持人';S.tripData=td;
    renderTrip();
  }catch(e){toast(e.message)}
}

function renderJoin(tripId){
  S.adminPwd=false;
  if(!tripId){
    app().innerHTML=`
<div class="header"><h1>前哨游学 OS</h1></div>
<div class="container">
  <div class="card" style="text-align:center;padding:40px 20px">
    <div style="font-size:48px;margin-bottom:16px">📱</div>
    <div style="font-size:16px;color:#666">请扫描主持人提供的二维码加入会议</div>
  </div>
</div>`;
    return;
  }
  api('GET','/meeting/api/trips/'+tripId).then(d=>{
    S.tripId=tripId;S.tripData=d;
    const saved=localStorage.getItem('meeting_name_'+tripId);
    const savedInfo=localStorage.getItem('meeting_userinfo_'+tripId);
    if(saved&&savedInfo){
      S.userName=saved;
      try{const o=JSON.parse(savedInfo);S.userIndustry=o.industry||'';S.userCompany=o.company||''}catch(e){}
      renderTrip();return;
    }
    if(saved&&!savedInfo){
      // 老用户补全公司/行业
      S.userName=saved;
      app().innerHTML=`
<div class="header"><h1>${d.name}</h1></div>
<div class="container">
  <div class="card">
    <h2>补充一下信息</h2>
    <div style="font-size:13px;color:#888;margin-bottom:14px">${esc(saved)}，再填两项就好（同学夜谈报名时大家能看到）</div>
    <div class="label">行业</div>
    <input id="joinIndustry" placeholder="例：服装制造、互联网、医疗" autofocus>
    <div class="gap"><div class="label">公司（可不填）</div>
    <input id="joinCompany" placeholder="可选"></div>
    <div class="gap"><button class="btn btn-primary" onclick="doCompleteInfo()">保存</button></div>
  </div>
</div>`;
      return;
    }
    app().innerHTML=`
<div class="header"><h1>${d.name}</h1></div>
<div class="container">
  <div class="card">
    <h2>加入</h2>
    <div class="label">姓名</div>
    <input id="joinName" placeholder="请输入姓名" autofocus>
    <div class="gap"><div class="label">行业</div>
    <input id="joinIndustry" placeholder="例：服装制造、互联网、医疗"></div>
    <div class="gap"><div class="label">公司（可不填）</div>
    <input id="joinCompany" placeholder="可选"></div>
    <div class="gap"><button class="btn btn-primary" onclick="doJoinByName()">进入</button></div>
  </div>
</div>`;
  }).catch(()=>{
    app().innerHTML=`<div class="header"><h1>前哨游学 OS</h1></div><div class="container"><div class="card"><div class="empty">行程不存在或已结束</div></div></div>`;
  });
}

async function doJoinByName(){
  const name=$('joinName').value.trim();
  const industry=$('joinIndustry').value.trim();
  const company=$('joinCompany').value.trim();
  if(!name){toast('请输入姓名');return}
  if(!industry){toast('请填写行业');return}
  S.userName=name;S.userIndustry=industry;S.userCompany=company;
  localStorage.setItem('meeting_name_'+S.tripId,name);
  localStorage.setItem('meeting_userinfo_'+S.tripId,JSON.stringify({industry,company}));
  renderTrip();
}

async function doCompleteInfo(){
  const industry=$('joinIndustry').value.trim();
  const company=$('joinCompany').value.trim();
  if(!industry){toast('请填写行业');return}
  S.userIndustry=industry;S.userCompany=company;
  localStorage.setItem('meeting_userinfo_'+S.tripId,JSON.stringify({industry,company}));
  renderTrip();
}

async function doCreate(){
  const name=$('tripName').value.trim();
  const pwd=$('tripPwd').value.trim();
  if(!name||!pwd){toast('请填写行程名称和密码');return}
  try{
    const d=await api('POST','/meeting/api/trips',{name,admin_password:pwd});
    S.tripId=d.trip_id;S.adminPwd=pwd;S.userName='主持人';
    saveAdminTrip(d.trip_id,name,pwd);
    const td=await api('GET','/meeting/api/trips/'+d.trip_id);
    S.tripData=td;
    renderTrip();
  }catch(e){toast(e.message)}
}

function copyJoinLink(){
  const url=location.origin+'/m?trip='+S.tripId;
  navigator.clipboard.writeText(url).then(()=>toast('链接已复制')).catch(()=>toast(url));
}

function saveQRCode(){
  const name=S.tripData?S.tripData.name:'行程';
  const url=location.origin+'/m?trip='+S.tripId;
  const size=240;
  const pad=20;
  const textH=52;
  const canvas=document.createElement('canvas');
  canvas.width=size+pad*2;
  canvas.height=size+pad*2+textH;
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#ffffff';
  ctx.fillRect(0,0,canvas.width,canvas.height);
  const img=new Image();
  img.crossOrigin='anonymous';
  img.onload=()=>{
    ctx.drawImage(img,pad,pad,size,size);
    ctx.fillStyle='#1a1a1a';
    ctx.font='bold 17px -apple-system,PingFang SC,sans-serif';
    ctx.textAlign='center';
    ctx.fillText(name,canvas.width/2,size+pad+30);
    ctx.fillStyle='#888';
    ctx.font='12px -apple-system,PingFang SC,sans-serif';
    ctx.fillText('扫码加入行程',canvas.width/2,size+pad+48);
    const dataUrl=canvas.toDataURL('image/png');
    const bg=document.createElement('div');
    bg.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px';
    bg.innerHTML=`<img src="${dataUrl}" style="width:280px;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.4)">
      <div style="color:#fff;font-size:15px;opacity:.9">长按图片保存到相册</div>
      <button onclick="this.parentNode.remove()" style="color:#fff;background:rgba(255,255,255,.2);border:none;border-radius:20px;padding:10px 28px;font-size:15px;cursor:pointer">关闭</button>`;
    document.body.appendChild(bg);
  };
  img.onerror=()=>toast('二维码加载失败，请截图保存');
  img.src='https://api.qrserver.com/v1/create-qr-code/?size='+size+'x'+size+'&data='+encodeURIComponent(url);
}

function renderTrip(){
  const t=S.tripData;
  const isAdmin=!!S.adminPwd;
  const joinUrl=location.origin+'/m?trip='+S.tripId;
  const qrUrl='https://api.qrserver.com/v1/create-qr-code/?size=180x180&data='+encodeURIComponent(joinUrl);
  const tab=S.tripTab||'schedule';
  const sched=Array.isArray(t.schedule)?t.schedule:[];
  const scheduleHtml=isAdmin?renderScheduleAdmin(sched):renderScheduleView(sched);
  const topicsEnabled=!!t.topics_enabled;
  const showTopicsTab=isAdmin||topicsEnabled;
  const topicsHtml=renderTopicsTab(t,isAdmin);
  const mentorsEnabled=!!t.mentors_enabled;
  const showMentorsTab=isAdmin||mentorsEnabled;
  const mentorsHtml=renderMentorsTab(t,isAdmin);
  let roomsHtml=t.rooms.length?t.rooms.map(r=>`
    <div class="room-card" onclick="enterRoom('${r.id}')">
      <div><div class="room-name">${r.name}</div><div class="room-meta">${r.msg_count} 条发言</div></div>
      <div class="arrow">›</div>
    </div>`).join(''):'<div class="empty">暂无议题</div>';
  const meetingHtml=`
    <div class="card"><h2>议题列表</h2>${roomsHtml}</div>
    ${isAdmin?`<div class="card"><h2>创建议题</h2>
      <div class="label">议题名称</div>
      <input id="roomName" placeholder="如：今日复盘、分组讨论A">
      <div class="gap"><button class="btn btn-secondary btn-sm" onclick="doCreateRoom()">创建</button></div>
    </div>`:''}`;
  app().innerHTML=`
<div class="header">
  ${isAdmin?'<button class="back-btn" onclick="renderHome()">‹</button>':'<span style="width:8px"></span>'}
  <h1>${t.name}</h1>
  ${isAdmin?'<span class="tag" style="margin-left:auto">管理员</span>':''}
</div>
<div class="container">
  ${isAdmin?`<div class="card"><h2>邀请参会者</h2>
    <div class="qr-box"><img src="${qrUrl}" alt="QR" id="qrImg"><div class="qr-url">行程码：<b>${S.tripId}</b></div></div>
    <div class="gap" style="display:flex;gap:8px">
      <button class="btn btn-secondary btn-sm" onclick="copyJoinLink()">复制参会链接</button>
      <button class="btn btn-secondary btn-sm" onclick="saveQRCode()">保存二维码</button>
    </div>
  </div>`:''}
  <div class="tabs">
    <div class="tab${tab==='schedule'?' active':''}" onclick="switchTab('schedule')">日程</div>
    <div class="tab${tab==='meeting'?' active':''}" onclick="switchTab('meeting')">议题</div>
    ${showTopicsTab?`<div class="tab${tab==='topics'?' active':''}" onclick="switchTab('topics')">同学夜谈</div>`:''}
    ${showMentorsTab?`<div class="tab${tab==='mentors'?' active':''}" onclick="switchTab('mentors')">导师评分</div>`:''}
  </div>
  ${tab==='schedule'?scheduleHtml:tab==='topics'?topicsHtml:tab==='mentors'?mentorsHtml:meetingHtml}
  ${!isAdmin&&S.adminPwd===undefined?'<div class="admin-toggle" onclick="showAdminLogin()">管理员登录</div>':''}
</div>`;
  if(isAdmin&&tab==='meeting'){}
  if(tab==='schedule'&&!isAdmin)setTimeout(scrollToToday,50);
}

function switchTab(t){S.tripTab=t;renderTrip();if(t==='schedule')setTimeout(scrollToToday,50)}

function esc(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function todayStr(){
  const d=new Date();
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
}

function todayIndex(sched){
  const t=todayStr();
  const i=sched.findIndex(x=>x.date===t);
  if(i>=0)return i;
  // 找最近过去的一天
  const past=sched.map((x,idx)=>({d:x.date,idx})).filter(x=>x.d&&x.d<=t).sort((a,b)=>b.d.localeCompare(a.d));
  if(past.length)return past[0].idx;
  return 0;
}

function dateLabel(d){
  if(!d)return '未设置日期';
  const parts=d.split('-');
  if(parts.length!==3)return d;
  return parts[1]+'月'+parseInt(parts[2],10)+'日';
}

function renderScheduleView(sched){
  if(!sched.length)return '<div class="card"><div class="empty">主持人还未发布日程</div></div>';
  const today=todayStr();
  const tIdx=todayIndex(sched);
  const dots=sched.map((x,i)=>`<span class="dot${i===tIdx?' active':''}${x.date===today?' today':''}" data-idx="${i}" onclick="goDay(${i})">Day${i+1}</span>`).join('');
  const pages=sched.map((x,i)=>`
    <div class="swipe-page">
      <div class="card">
        <div class="day-head">
          <div class="day-title">Day ${i+1}${x.date===today?' · 今天':''}</div>
          <div class="day-date">${esc(dateLabel(x.date))}</div>
        </div>
        ${x.content?`<div class="schedule-text">${esc(x.content)}</div>`:'<div class="empty">这一天暂无安排</div>'}
      </div>
    </div>`).join('');
  return `<div class="dots" id="schedDots">${dots}</div>
    <div class="swipe-wrap"><div class="swipe" id="schedSwipe" onscroll="onSwipeScroll()">${pages}</div></div>`;
}

function renderScheduleAdmin(sched){
  const rows=sched.map((x,i)=>`
    <div class="card" style="border:1.5px solid #e8e8ed">
      <div class="day-head">
        <div class="day-title">Day ${i+1}</div>
        <button class="btn-sm" style="background:none;border:none;color:#ff3b30;font-size:14px;font-weight:600;cursor:pointer" onclick="delDay(${i})">删除</button>
      </div>
      <div class="label">日期</div>
      <input type="date" value="${esc(x.date)}" onchange="setDayField(${i},'date',this.value)">
      <div class="gap"><div class="label">内容</div>
      <textarea rows="6" placeholder="9:00 集合出发&#10;10:00 参观XX博物馆..." oninput="setDayField(${i},'content',this.value)">${esc(x.content)}</textarea></div>
    </div>`).join('');
  return `${rows||'<div class="card"><div class="empty">还没有添加任何一天</div></div>'}
    <div style="display:flex;gap:8px;margin-top:4px">
      <button class="btn btn-secondary btn-sm" onclick="addDay()">+ 添加一天</button>
      <button class="btn btn-primary btn-sm" onclick="doSaveSchedule()">保存全部</button>
    </div>`;
}

function setDayField(i,k,v){S.tripData.schedule[i][k]=v}
function addDay(){
  const s=S.tripData.schedule;
  let nextDate='';
  if(s.length){
    const last=s[s.length-1].date;
    if(last){const d=new Date(last);d.setDate(d.getDate()+1);nextDate=d.toISOString().slice(0,10)}
  }else{nextDate=todayStr()}
  s.push({date:nextDate,content:''});
  renderTrip();
}
function delDay(i){
  if(!confirm('确认删除 Day '+(i+1)+'？'))return;
  S.tripData.schedule.splice(i,1);renderTrip();
}

function goDay(i){
  const sw=$('schedSwipe');if(!sw)return;
  sw.scrollTo({left:sw.clientWidth*i,behavior:'smooth'});
}

function scrollToToday(){
  const sw=$('schedSwipe');if(!sw||!S.tripData)return;
  const i=todayIndex(S.tripData.schedule||[]);
  sw.scrollLeft=sw.clientWidth*i;
}

function onSwipeScroll(){
  const sw=$('schedSwipe');if(!sw)return;
  const i=Math.round(sw.scrollLeft/sw.clientWidth);
  const dots=document.querySelectorAll('#schedDots .dot');
  dots.forEach((d,idx)=>d.classList.toggle('active',idx===i));
}

async function doSaveSchedule(){
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/schedule',{admin_password:S.adminPwd,schedule:S.tripData.schedule});
    toast('日程已保存');
  }catch(e){toast(e.message)}
}

// ─── 同学夜谈 ─────────────────────────────────────────────
function renderTopicsTab(t,isAdmin){
  const enabled=!!t.topics_enabled;
  const proposeOpen=t.propose_open!==false;
  const cur=t.current_round||1;
  const topics=Array.isArray(t.topics)?t.topics:[];
  let adminBar='';
  if(isAdmin){
    adminBar=`<div class="card" style="background:#fff8f0">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div>
          <div style="font-size:15px;font-weight:600">同学夜谈 ${enabled?'<span class="topic-tag" style="background:#34c759;color:#fff">已开启</span>':'<span class="topic-tag gray">未开启</span>'}</div>
          <div style="font-size:13px;color:#888;margin-top:3px">当前第 ${cur} 轮 · 提报${proposeOpen?'<span style="color:#34c759">开放中</span>':'<span style="color:#ff3b30">已关闭</span>'}</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-secondary btn-sm" onclick="doToggleTopics(${!enabled})">${enabled?'关闭':'开启'}</button>
          ${enabled?`<button class="btn btn-secondary btn-sm" onclick="doToggleProposeOpen(${!proposeOpen})">${proposeOpen?'停止提报':'重新开放提报'}</button>`:''}
          ${enabled?`<button class="btn btn-secondary btn-sm" onclick="doNewRound()">开启新一轮</button>`:''}
        </div>
      </div>
    </div>`;
  }
  if(!enabled&&!isAdmin){
    return '<div class="card"><div class="empty">同学夜谈尚未开启</div></div>';
  }
  // 按轮次倒序分组
  const byRound={};
  topics.forEach(tp=>{(byRound[tp.round]=byRound[tp.round]||[]).push(tp)});
  const rounds=Object.keys(byRound).map(n=>parseInt(n,10)).sort((a,b)=>b-a);
  let listHtml='';
  if(!rounds.length){
    listHtml='<div class="card"><div class="empty">还没有人提报话题</div></div>';
  }else{
    rounds.forEach(rn=>{
      const isCur=rn===cur;
      listHtml+=`<div class="round-head">
        第 ${rn} 轮
        <span class="round-tag${isCur?'':' past'}">${isCur?'进行中':'已结束'}</span>
      </div>`;
      const roundTopics=byRound[rn].slice().sort((a,b)=>(a.cancelled?1:0)-(b.cancelled?1:0)||(b.signups?.length||0)-(a.signups?.length||0));
      roundTopics.forEach(tp=>{listHtml+=topicCardHtml(tp,isAdmin,isCur)});
    });
  }
  let proposeForm='';
  if(enabled&&proposeOpen){
    const myInfo=[S.userIndustry,S.userCompany].filter(Boolean).join(' · ');
    proposeForm=`<div class="card"><h2>提报话题（第 ${cur} 轮）</h2>
      <div style="font-size:13px;color:#888;margin-bottom:10px">将以「${esc(S.userName||'')}${myInfo?' · '+esc(myInfo):''}」身份提报</div>
      <div class="label">分享标题</div>
      <input id="topicTitle" placeholder="例：服装制造业需要什么样的AI">
      <div class="gap"><div class="label">公司 / 角色（可选，覆盖注册时填的）</div>
      <input id="topicCompany" placeholder="留空则用注册时填的"></div>
      <div class="gap"><div class="label">话题简介（一两句话）</div>
      <textarea id="topicDesc" rows="3" placeholder="写得越具体，大家越容易判断是否感兴趣"></textarea></div>
      <div class="gap"><button class="btn btn-primary btn-sm" onclick="doProposeTopic()">提交话题</button></div>
      <div style="font-size:12px;color:#888;margin-top:8px">每人本轮只能提报一次。提报后即不能再报名其他话题。</div>
    </div>`;
  }
  return adminBar+proposeForm+listHtml;
}

function topicCardHtml(tp,isAdmin,isCurRound){
  const signups=(tp.signups||[]).map(s=>typeof s==='string'?{name:s,industry:'',company:''}:s);
  const cnt=signups.length;
  const mySignedUp=S.userName&&signups.some(s=>s.name===S.userName);
  const meIsProposer=S.userName===tp.proposer;
  const cls='topic-card'+(tp.cancelled?' cancelled':tp.full?' full':'');
  const tags=[];
  if(tp.cancelled)tags.push('<span class="topic-tag danger">已取消</span>');
  else if(tp.full)tags.push('<span class="topic-tag gray">已满</span>');
  let action='';
  if(!tp.cancelled&&isCurRound){
    if(meIsProposer){
      action='<span class="topic-tag" style="background:#fff5e0;color:#ff9500">你是分享者</span>';
    }else if(mySignedUp){
      action=`<button class="btn btn-secondary btn-sm" onclick="doUnsignup('${tp.id}')">取消报名</button>`;
    }else if(!tp.full){
      action=`<button class="btn btn-primary btn-sm" onclick="doSignup('${tp.id}')">报名</button>`;
    }
  }
  let adminCtl='';
  if(isAdmin&&!tp.cancelled){
    adminCtl=`<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
      <button class="btn btn-secondary btn-sm" onclick="doToggleFull('${tp.id}')">${tp.full?'解除满员':'设为满员'}</button>
      <button class="btn-delete-mini" onclick="doDeleteTopic('${tp.id}')" style="background:none;border:1px solid #ffccc7;color:#ff3b30;border-radius:8px;padding:8px 14px;font-size:14px;font-weight:600;cursor:pointer">删除</button>
    </div>`;
  }
  if(isAdmin&&tp.cancelled){
    adminCtl=`<div style="margin-top:8px"><button class="btn-delete-mini" onclick="doDeleteTopic('${tp.id}')" style="background:none;border:1px solid #ffccc7;color:#ff3b30;border-radius:8px;padding:8px 14px;font-size:14px;font-weight:600;cursor:pointer">彻底删除</button></div>`;
  }
  const proposerLine=[tp.industry,tp.company].filter(Boolean).join(' · ');
  const namesHtml=cnt&&!tp.cancelled?`<div class="signup-names">${signups.map(s=>{
    const tail=[s.industry,s.company].filter(Boolean).join(' · ');
    return `<span>${esc(s.name)}${tail?' · '+esc(tail):''}</span>`;
  }).join('')}</div>`:'';
  return `<div class="${cls}">
    <div class="topic-title">${esc(tp.title)}${tags.join('')}</div>
    <div class="topic-meta">${esc(tp.proposer)}${proposerLine?' · '+esc(proposerLine):''}</div>
    ${tp.desc?`<div class="topic-desc">${esc(tp.desc)}</div>`:''}
    <div class="topic-foot">
      <span class="topic-count">${cnt} / 6 人</span>
      ${action}
    </div>
    ${namesHtml}
    ${adminCtl}
  </div>`;
}

async function refreshTrip(){
  try{S.tripData=await api('GET','/meeting/api/trips/'+S.tripId);renderTrip()}catch(e){toast(e.message)}
}

async function doToggleTopics(enabled){
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/enable',{admin_password:S.adminPwd,enabled});
    await refreshTrip();
    toast(enabled?'同学夜谈已开启':'已关闭');
  }catch(e){toast(e.message)}
}

async function doNewRound(){
  if(!confirm('开启新一轮？当前轮的话题会标记为"已结束"，新话题进入下一轮。'))return;
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/new_round',{admin_password:S.adminPwd});
    await refreshTrip();
    toast('已开启新一轮');
  }catch(e){toast(e.message)}
}

async function doToggleProposeOpen(open){
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/propose_open',{admin_password:S.adminPwd,open});
    await refreshTrip();
    toast(open?'提报已重新开放':'提报已关闭');
  }catch(e){toast(e.message)}
}

async function doProposeTopic(){
  const title=$('topicTitle').value.trim();
  const company=$('topicCompany').value.trim()||S.userCompany||'';
  const desc=$('topicDesc').value.trim();
  if(!S.userName){toast('请先填写姓名');return}
  if(!title){toast('请填写分享标题');return}
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics',{proposer:S.userName,company,industry:S.userIndustry||'',title,desc});
    toast('提报成功');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doSignup(topicId){
  if(!S.userName){toast('请先填写姓名');return}
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/'+topicId+'/signup',{user_name:S.userName,industry:S.userIndustry||'',company:S.userCompany||''});
    toast('报名成功');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doUnsignup(topicId){
  if(!S.userName)return;
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/'+topicId+'/unsignup',{user_name:S.userName});
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doToggleFull(topicId){
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/'+topicId+'/toggle_full',{admin_password:S.adminPwd,topic_id:topicId});
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doCancelTopic(topicId){
  if(!confirm('确认取消这个话题？已报名的人会看到"已取消"提示。'))return;
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/'+topicId+'/cancel',{admin_password:S.adminPwd,topic_id:topicId});
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doDeleteTopic(topicId){
  if(!confirm('彻底删除这个话题？删除后无法恢复，所有报名记录也会一起清掉。'))return;
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/topics/'+topicId+'/delete',{admin_password:S.adminPwd,topic_id:topicId});
    toast('已删除');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

// ─── 导师评分 ─────────────────────────────────────────────
const RATING_DIMS=[
  {key:'content',label:'内容价值',hint:'对本次硅谷 AI 考察是否有实际启发'},
  {key:'depth',label:'专业深度',hint:'是否体现出行业经验、认知深度和判断力'},
  {key:'case',label:'案例 / 信息量',hint:'是否有真实案例、一手信息、具体经验'},
  {key:'delivery',label:'表达与节奏',hint:'讲得是否清楚，重点是否突出，时间节奏是否合适'},
  {key:'interaction',label:'互动回应',hint:'回答问题是否直接、有料、愿意交流'},
  {key:'overall',label:'综合评分',hint:'你对这位导师的总体评价'},
  {key:'again',label:'下次还想听',hint:'如果他下次还来，你有多想听'},
];

function renderMentorsTab(t,isAdmin){
  const enabled=!!t.mentors_enabled;
  const mentors=Array.isArray(t.mentors)?t.mentors:[];
  let adminBar='';
  if(isAdmin){
    adminBar=`<div class="card" style="background:#fff8f0">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div>
          <div style="font-size:15px;font-weight:600">导师评分 ${enabled?'<span class="topic-tag" style="background:#34c759;color:#fff">已开启</span>':'<span class="topic-tag gray">未开启</span>'}</div>
          <div style="font-size:13px;color:#888;margin-top:3px">${mentors.length} 位导师</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-secondary btn-sm" onclick="exportRatings()">导出明细</button>
          <button class="btn btn-secondary btn-sm" onclick="doToggleMentors(${!enabled})">${enabled?'关闭':'开启'}</button>
        </div>
      </div>
    </div>`;
  }
  if(!enabled&&!isAdmin){
    return '<div class="card"><div class="empty">导师评分尚未开启</div></div>';
  }
  if(!mentors.length){
    const emptyMsg=isAdmin?'还没添加导师，下方添加':'还没有导师';
    let addForm='';
    if(isAdmin){
      addForm=`<div class="card"><h2>添加导师</h2>
        <div class="label">姓名</div>
        <input id="mentorName" placeholder="例：李大伟">
        <div class="gap"><div class="label">简介（可选）</div>
        <input id="mentorBio" placeholder="例：Google AI 主管 · 5月13日讲课"></div>
        <div class="gap"><button class="btn btn-primary btn-sm" onclick="doAddMentor()">添加</button></div>
      </div>`;
    }
    return adminBar+'<div class="card"><div class="empty">'+emptyMsg+'</div></div>'+addForm;
  }
  // 排行：按综合分倒序，没人评的放最后
  const ranked=mentors.slice().sort((a,b)=>{
    const ao=a.avgs&&a.avgs.overall;const bo=b.avgs&&b.avgs.overall;
    if(ao==null&&bo==null)return 0;
    if(ao==null)return 1;
    if(bo==null)return -1;
    return bo-ao;
  });
  const rankCards=ranked.map((m,i)=>rankCardHtml(m,i,isAdmin)).join('');
  const commentBlocks=ranked.map(m=>commentBlockHtml(m)).join('');
  let mgmtSection='';
  if(isAdmin){
    mgmtSection=`<div style="margin-top:18px;font-size:14px;font-weight:600;color:#888;letter-spacing:1px;padding:0 4px">管理</div>`
      +mentors.map((m,i)=>mentorCardHtml(m,true,i,mentors.length)).join('')
      +`<div class="card"><h2>添加导师</h2>
        <div class="label">姓名</div>
        <input id="mentorName" placeholder="例：李大伟">
        <div class="gap"><div class="label">简介（可选）</div>
        <input id="mentorBio" placeholder="例：Google AI 主管 · 5月13日讲课"></div>
        <div class="gap"><button class="btn btn-primary btn-sm" onclick="doAddMentor()">添加</button></div>
      </div>`;
  }
  return adminBar
    +'<div class="card"><h2>导师排行榜</h2>'+rankCards+'</div>'
    +'<div class="card"><h2>同学评价</h2>'+commentBlocks+'</div>'
    +mgmtSection;
}

function rankCardHtml(m,idx,isAdmin){
  const cnt=m.count||0;
  const overall=m.avgs&&m.avgs.overall;
  const initial=(m.name||'?').slice(0,1);
  const avatar=m.avatar
    ?`<img src="${esc(m.avatar)}" alt="" onclick="event.stopPropagation();showAvatarLarge('${esc(m.avatar)}')" style="width:48px;height:48px;border-radius:50%;object-fit:cover;cursor:pointer;flex-shrink:0">`
    :`<div style="width:48px;height:48px;border-radius:50%;background:#007aff;color:#fff;font-size:22px;font-weight:600;display:flex;align-items:center;justify-content:center;flex-shrink:0">${esc(initial)}</div>`;
  const medals=['🥇','🥈','🥉'];
  const rank=cnt>0?(medals[idx]||`<span style="font-size:15px;font-weight:700;color:#888">${idx+1}</span>`):'<span style="font-size:13px;color:#ccc">—</span>';
  const scoreHtml=cnt>0
    ?`<span style="font-size:22px;font-weight:700;color:#007aff">${overall!=null?overall:'-'}</span><span style="font-size:13px;color:#888;margin-left:2px">/5 · ${cnt}人</span>`
    :'<span style="font-size:13px;color:#aaa">暂无评分</span>';
  const myRated=cnt>0&&S.userName;
  const btnLabel=isAdmin?'查看详情':(myRated?'修改我的评分':'去评分');
  const btnAction=isAdmin?`showMentorDetail('${m.id}')`:`openRatingForm('${m.id}')`;
  return `<div style="display:flex;align-items:center;gap:12px;padding:12px 4px;border-bottom:1px solid #f0f0f5">
    <div style="width:28px;text-align:center;font-size:22px;flex-shrink:0">${rank}</div>
    ${avatar}
    <div style="flex:1;min-width:0">
      <div style="font-size:16px;font-weight:600;color:#1a1a1a">${esc(m.name)}</div>
      ${m.bio?`<div style="font-size:13px;color:#888;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.bio)}</div>`:''}
      <div style="margin-top:4px">${scoreHtml}</div>
    </div>
    <button class="btn btn-secondary btn-sm" style="flex-shrink:0" onclick="${btnAction}">${btnLabel}</button>
  </div>`;
}

function commentBlockHtml(m){
  const comments=m.comments||[];
  if(!comments.length){
    return `<div style="padding:12px 0;border-bottom:1px solid #f0f0f5">
      <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:6px">${esc(m.name)}</div>
      <div style="font-size:13px;color:#aaa">还没有同学留言</div>
    </div>`;
  }
  return `<div style="padding:12px 0;border-bottom:1px solid #f0f0f5">
    <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:8px">${esc(m.name)}<span style="font-size:13px;color:#888;font-weight:normal"> · ${comments.length} 条留言</span></div>
    ${comments.map(c=>`<div class="comment-item">${esc(c)}</div>`).join('')}
  </div>`;
}

function mentorCardHtml(m,isAdmin,idx,total){
  const cnt=m.count||0;
  const overall=m.avgs&&m.avgs.overall;
  const statsHtml=cnt>0?`<div class="mentor-stats">
    综合评分 <span class="num">${overall!=null?overall:'-'}</span> / 5 · 来自 ${cnt} 位同学
  </div>`:'<div class="mentor-stats" style="color:#aaa">还没有同学评分</div>';
  const initial=(m.name||'?').slice(0,1);
  const avatarHtml=m.avatar
    ?`<img src="${esc(m.avatar)}" alt="" onclick="showAvatarLarge('${esc(m.avatar)}')" style="width:56px;height:56px;border-radius:50%;object-fit:cover;flex-shrink:0;cursor:pointer">`
    :`<div style="width:56px;height:56px;border-radius:50%;background:#007aff;color:#fff;font-size:24px;font-weight:600;display:flex;align-items:center;justify-content:center;flex-shrink:0">${esc(initial)}</div>`;
  let actions='';
  if(isAdmin){
    const upDis=idx===0?'opacity:.3;pointer-events:none':'';
    const downDis=idx===total-1?'opacity:.3;pointer-events:none':'';
    actions=`<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <button class="btn btn-secondary btn-sm" style="padding:8px 12px;${upDis}" onclick="moveMentor('${m.id}',-1)">↑</button>
      <button class="btn btn-secondary btn-sm" style="padding:8px 12px;${downDis}" onclick="moveMentor('${m.id}',1)">↓</button>
      <button class="btn btn-secondary btn-sm" onclick="pickMentorAvatar('${m.id}')">${m.avatar?'更换头像':'上传头像'}</button>
      <button class="btn btn-secondary btn-sm" onclick="showMentorDetail('${m.id}')">查看详情</button>
      <button class="btn-delete-mini" onclick="doDeleteMentor('${m.id}','${esc(m.name).replace(/'/g,"&#39;")}')" style="background:none;border:1px solid #ffccc7;color:#ff3b30;border-radius:8px;padding:8px 14px;font-size:14px;font-weight:600;cursor:pointer">删除</button>
    </div>`;
  }else{
    actions=`<button class="btn btn-primary btn-sm" onclick="openRatingForm('${m.id}')">${cnt>0&&S.userName?'修改我的评分':'去评分'}</button>`;
  }
  return `<div class="mentor-card">
    <div style="display:flex;gap:12px;align-items:flex-start">
      ${avatarHtml}
      <div style="flex:1;min-width:0">
        <div class="mentor-name">${esc(m.name)}</div>
        ${m.bio?`<div class="mentor-bio">${esc(m.bio)}</div>`:''}
        ${statsHtml}
      </div>
    </div>
    <div class="mentor-foot">${actions}</div>
  </div>`;
}

async function moveMentor(mentorId,delta){
  const mentors=(S.tripData.mentors||[]).slice();
  const i=mentors.findIndex(m=>m.id===mentorId);
  const j=i+delta;
  if(i<0||j<0||j>=mentors.length)return;
  [mentors[i],mentors[j]]=[mentors[j],mentors[i]];
  const order=mentors.map(m=>m.id);
  // 乐观更新
  S.tripData.mentors=mentors;
  renderTrip();
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/mentors/reorder',{admin_password:S.adminPwd,order});
  }catch(e){toast(e.message);await refreshTrip()}
}

function pickMentorAvatar(mentorId){
  let input=document.getElementById('mentorAvatarInput');
  if(!input){
    input=document.createElement('input');
    input.type='file';input.accept='image/*';input.id='mentorAvatarInput';input.style.display='none';
    document.body.appendChild(input);
  }
  input.onchange=()=>{
    const file=input.files&&input.files[0];
    input.value='';
    if(!file)return;
    if(file.size>50*1024*1024){toast('图片不能超过 50MB');return}
    openCropper(file,mentorId);
  };
  input.click();
}

function openCropper(file,mentorId){
  const url=URL.createObjectURL(file);
  const VIEW=260,OUT=400;
  const bg=document.createElement('div');
  bg.className='modal-backdrop';
  bg.style.alignItems='center';
  bg.innerHTML=`<div onclick="event.stopPropagation()" style="background:#fff;border-radius:16px;width:92%;max-width:340px;padding:18px">
    <div class="modal-head">
      <h3>调整头像</h3>
      <button class="modal-close" onclick="closeCropper()">×</button>
    </div>
    <div style="font-size:13px;color:#888;margin-bottom:10px">拖动移动 · 滑块缩放</div>
    <div id="cropView" style="position:relative;width:${VIEW}px;height:${VIEW}px;margin:0 auto;background:#000;border-radius:50%;overflow:hidden;touch-action:none;cursor:grab;user-select:none">
      <img id="cropImg" draggable="false" style="position:absolute;left:50%;top:50%;user-select:none;-webkit-user-drag:none;pointer-events:none">
    </div>
    <div style="margin:14px 0 10px;display:flex;align-items:center;gap:10px">
      <span style="font-size:14px;color:#888">缩放</span>
      <input type="range" id="cropScale" min="100" max="300" value="100" style="flex:1">
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-secondary" style="flex:1" onclick="closeCropper()">取消</button>
      <button class="btn btn-primary" style="flex:1" onclick="confirmCrop('${mentorId}')">确认上传</button>
    </div>
  </div>`;
  document.body.appendChild(bg);
  const img=document.getElementById('cropImg');
  const view=document.getElementById('cropView');
  const slider=document.getElementById('cropScale');
  let baseScale=1,scale=1,tx=0,ty=0,iw=0,ih=0;
  function paint(){img.style.transform=`translate(calc(-50% + ${tx}px),calc(-50% + ${ty}px)) scale(${baseScale*scale})`}
  function setup(){
    iw=img.naturalWidth;ih=img.naturalHeight;
    if(!iw||!ih){toast('图片加载失败');return}
    baseScale=Math.max(VIEW/iw,VIEW/ih);
    img.style.width=iw+'px';img.style.height=ih+'px';
    paint();
  }
  img.onload=setup;
  img.onerror=()=>toast('图片加载失败');
  img.src=url;
  if(img.complete&&img.naturalWidth)setup();
  slider.oninput=()=>{scale=slider.value/100;paint()};
  let drag=false,sx=0,sy=0,stx=0,sty=0;
  function pd(e){const p=e.touches?e.touches[0]:e;drag=true;sx=p.clientX;sy=p.clientY;stx=tx;sty=ty;view.style.cursor='grabbing'}
  function pm(e){if(!drag)return;e.preventDefault();const p=e.touches?e.touches[0]:e;tx=stx+(p.clientX-sx);ty=sty+(p.clientY-sy);paint()}
  function pu(){drag=false;view.style.cursor='grab'}
  view.addEventListener('mousedown',pd);
  view.addEventListener('touchstart',pd,{passive:false});
  view.addEventListener('touchmove',pm,{passive:false});
  view.addEventListener('touchend',pu);
  document.addEventListener('mousemove',pm);
  document.addEventListener('mouseup',pu);
  bg._crop={img,VIEW,OUT,url,get tx(){return tx},get ty(){return ty},get bs(){return baseScale},get s(){return scale},get iw(){return iw},get ih(){return ih},cleanup(){document.removeEventListener('mousemove',pm);document.removeEventListener('mouseup',pu);URL.revokeObjectURL(url)}};
}

function closeCropper(){
  const bg=document.querySelector('.modal-backdrop');
  if(bg&&bg._crop)bg._crop.cleanup();
  if(bg)bg.remove();
}

async function confirmCrop(mentorId){
  const bg=document.querySelector('.modal-backdrop');
  if(!bg||!bg._crop)return;
  const c=bg._crop;
  const fs=c.bs*c.s;
  const srcSize=c.VIEW/fs;
  const srcX=c.iw/2-c.tx/fs-srcSize/2;
  const srcY=c.ih/2-c.ty/fs-srcSize/2;
  const canvas=document.createElement('canvas');
  canvas.width=c.OUT;canvas.height=c.OUT;
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#fff';ctx.fillRect(0,0,c.OUT,c.OUT);
  ctx.drawImage(c.img,srcX,srcY,srcSize,srcSize,0,0,c.OUT,c.OUT);
  c.cleanup();bg.remove();
  canvas.toBlob(async(blob)=>{
    if(!blob){toast('裁剪失败');return}
    const fd=new FormData();
    fd.append('admin_password',S.adminPwd);
    fd.append('file',new File([blob],'avatar.jpg',{type:'image/jpeg'}));
    toast('上传中...',5000);
    try{
      const r=await fetch('/meeting/api/trips/'+S.tripId+'/mentors/'+mentorId+'/avatar',{method:'POST',body:fd});
      const d=await r.json();
      if(!r.ok)throw new Error(d.detail||'上传失败');
      toast('头像已更新');
      await refreshTrip();
    }catch(e){toast(e.message)}
  },'image/jpeg',0.88);
}

async function doToggleMentors(enabled){
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/mentors/enable',{admin_password:S.adminPwd,enabled});
    await refreshTrip();
    toast(enabled?'导师评分已开启':'已关闭');
  }catch(e){toast(e.message)}
}

function exportRatings(){
  const url='/meeting/api/trips/'+S.tripId+'/mentors/export?admin_password='+encodeURIComponent(S.adminPwd);
  const a=document.createElement('a');
  a.href=url;
  a.download='';
  document.body.appendChild(a);a.click();a.remove();
}

async function doAddMentor(){
  const name=$('mentorName').value.trim();
  const bio=$('mentorBio').value.trim();
  if(!name){toast('请填写导师姓名');return}
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/mentors',{admin_password:S.adminPwd,name,bio});
    $('mentorName').value='';$('mentorBio').value='';
    toast('已添加');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function doDeleteMentor(mentorId,name){
  if(!confirm('删除导师「'+name+'」及其所有评分？此操作不可恢复。'))return;
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/mentors/'+mentorId+'/delete',{admin_password:S.adminPwd});
    toast('已删除');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

function closeModal(){const el=document.querySelector('.modal-backdrop');if(el)el.remove()}

function showAvatarLarge(url){
  const el=document.createElement('div');
  el.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:300;display:flex;align-items:center;justify-content:center;padding:20px;cursor:zoom-out';
  el.onclick=()=>el.remove();
  el.innerHTML=`<img src="${url}" style="max-width:100%;max-height:100%;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.5)">`;
  document.body.appendChild(el);
}

async function openRatingForm(mentorId){
  if(!S.userName){toast('请先填写姓名');return}
  const mentor=(S.tripData.mentors||[]).find(m=>m.id===mentorId);
  if(!mentor)return;
  // 拉取当前用户已有评分
  let myScores={};let myComment='';
  try{
    const d=await api('GET','/meeting/api/trips/'+S.tripId+'/mentors/'+mentorId+'/my_rating?rater='+encodeURIComponent(S.userName));
    if(d.rating){myScores=d.rating.scores||{};myComment=d.rating.comment||''}
  }catch(e){}
  const rowsHtml=RATING_DIMS.map(d=>{
    const v=myScores[d.key]||0;
    const stars=[1,2,3,4,5].map(i=>`<span class="star-btn${i<=v?' on':''}" data-key="${d.key}" data-val="${i}" onclick="setStar('${d.key}',${i})">★</span>`).join('');
    return `<div style="margin-bottom:14px">
      <div style="font-size:15px;font-weight:600;color:#1a1a1a">${d.label}</div>
      <div style="font-size:13px;color:#888;margin:2px 0 6px">${d.hint}</div>
      <div class="star-btns" id="stars-${d.key}">${stars}</div>
    </div>`;
  }).join('');
  const initial=(mentor.name||'?').slice(0,1);
  const avatarBig=mentor.avatar
    ?`<img src="${esc(mentor.avatar)}" alt="" onclick="showAvatarLarge('${esc(mentor.avatar)}')" style="width:64px;height:64px;border-radius:50%;object-fit:cover;cursor:pointer">`
    :`<div style="width:64px;height:64px;border-radius:50%;background:#007aff;color:#fff;font-size:28px;font-weight:600;display:flex;align-items:center;justify-content:center">${esc(initial)}</div>`;
  const bg=document.createElement('div');
  bg.className='modal-backdrop';
  bg.onclick=e=>{if(e.target===bg)closeModal()};
  bg.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()">
    <div class="modal-head">
      <h3>评价导师</h3>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:14px">
      ${avatarBig}
      <div>
        <div style="font-size:18px;font-weight:600">${esc(mentor.name)}</div>
        ${mentor.bio?`<div style="font-size:13px;color:#888;margin-top:2px">${esc(mentor.bio)}</div>`:''}
        <div style="font-size:12px;color:#aaa;margin-top:4px">匿名提交，导师只看到平均分</div>
      </div>
    </div>
    ${rowsHtml}
    <div style="margin-top:8px"><div class="label">想对导师说一句话（可选）</div>
    <textarea id="ratingComment" rows="4" placeholder="可写收获、建议、印象最深的一点">${esc(myComment)}</textarea></div>
    <div style="margin-top:14px"><button class="btn btn-primary" onclick="submitRating('${mentorId}')">提交评分</button></div>
  </div>`;
  document.body.appendChild(bg);
}

function setStar(key,val){
  const wrap=document.getElementById('stars-'+key);
  if(!wrap)return;
  wrap.querySelectorAll('.star-btn').forEach((el,i)=>el.classList.toggle('on',i<val));
  wrap.dataset.val=val;
}

async function submitRating(mentorId){
  const scores={};
  for(const d of RATING_DIMS){
    const wrap=document.getElementById('stars-'+d.key);
    const v=parseInt(wrap?.dataset.val||'0',10);
    if(!v||v<1||v>5){toast('请给「'+d.label+'」打分');return}
    scores[d.key]=v;
  }
  const comment=$('ratingComment').value.trim();
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/mentors/'+mentorId+'/rate',{rater:S.userName,scores,comment});
    closeModal();
    toast('评分已提交');
    await refreshTrip();
  }catch(e){toast(e.message)}
}

async function showMentorDetail(mentorId){
  try{
    const d=await api('GET','/meeting/api/trips/'+S.tripId+'/mentors/admin?admin_password='+encodeURIComponent(S.adminPwd));
    const mentor=(d.mentors||[]).find(m=>m.id===mentorId);
    if(!mentor){toast('导师不存在');return}
    const avgs=mentor.avgs||{};
    const scoreRows=RATING_DIMS.map(dim=>{
      const v=avgs[dim.key];
      return `<div class="score-row">
        <div class="lbl">${dim.label}<small>${dim.hint}</small></div>
        <div class="score-bar"><span class="num">${v!=null?v:'-'}</span></div>
      </div>`;
    }).join('');
    const comments=(mentor.ratings||[]).map(r=>r.comment).filter(Boolean);
    const commentsHtml=comments.length?`<div style="margin-top:18px">
      <div style="font-size:15px;font-weight:600;margin-bottom:8px">同学反馈（匿名 · ${comments.length} 条）</div>
      <div class="comment-list">${comments.map(c=>`<div class="comment-item">${esc(c)}</div>`).join('')}</div>
    </div>`:'<div style="margin-top:18px;font-size:13px;color:#aaa">还没有文字反馈</div>';
    const bg=document.createElement('div');
    bg.className='modal-backdrop';
    bg.onclick=e=>{if(e.target===bg)closeModal()};
    bg.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()">
      <div class="modal-head">
        <h3>${esc(mentor.name)} · 评分详情</h3>
        <button class="modal-close" onclick="closeModal()">×</button>
      </div>
      ${mentor.bio?`<div style="font-size:13px;color:#888;margin-bottom:10px">${esc(mentor.bio)}</div>`:''}
      <div style="font-size:14px;color:#666;margin-bottom:6px">已收到 <b style="color:#007aff">${mentor.count}</b> 位同学评分</div>
      ${scoreRows}
      ${commentsHtml}
    </div>`;
    document.body.appendChild(bg);
  }catch(e){toast(e.message)}
}

function showAdminLogin(){
  const bg=document.createElement('div');
  bg.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;display:flex;align-items:center;justify-content:center;padding:20px';
  bg.id='adminLoginBg';
  bg.innerHTML=`<div style="background:#fff;border-radius:16px;padding:24px;width:100%;max-width:320px">
    <div style="font-size:17px;font-weight:600;margin-bottom:16px">管理员登录</div>
    <input id="adminPwdInput" type="password" placeholder="输入管理员密码" style="width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:15px;outline:none;margin-bottom:12px">
    <div style="display:flex;gap:10px">
      <button onclick="document.getElementById('adminLoginBg').remove()" style="flex:1;padding:12px;border:none;border-radius:10px;background:#f0f0f5;font-size:15px;font-weight:600;cursor:pointer">取消</button>
      <button onclick="confirmAdminLogin()" style="flex:1;padding:12px;border:none;border-radius:10px;background:#007aff;color:#fff;font-size:15px;font-weight:600;cursor:pointer">确认</button>
    </div>
  </div>`;
  document.body.appendChild(bg);
  setTimeout(()=>document.getElementById('adminPwdInput')&&document.getElementById('adminPwdInput').focus(),100);
}

function confirmAdminLogin(){
  const input=document.getElementById('adminPwdInput');
  if(!input)return;
  const pwd=input.value.trim();
  if(!pwd){toast('请输入密码');return}
  document.getElementById('adminLoginBg')?.remove();
  api('GET','/meeting/api/trips/'+S.tripId+'/prompt?admin_password='+encodeURIComponent(pwd))
    .then(()=>{S.adminPwd=pwd;toast('管理员模式已开启');renderTrip()})
    .catch(()=>{toast('密码错误')});
}

async function loadPrompt(){
  try{
    const d=await api('GET','/meeting/api/rooms/'+S.roomId+'/prompt?admin_password='+encodeURIComponent(S.adminPwd));
    if($('promptEdit'))$('promptEdit').value=d.prompt;
  }catch(e){}
}

async function doSavePrompt(){
  const p=$('promptEdit').value.trim();
  if(!p){toast('提示词不能为空');return}
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/prompt',{admin_password:S.adminPwd,prompt:p});toast('提示词已保存')}
  catch(e){toast(e.message)}
}

async function doResetPrompt(){
  try{
    await api('POST','/meeting/api/rooms/'+S.roomId+'/prompt',{admin_password:S.adminPwd,prompt:null});
    await loadPrompt();toast('已恢复默认提示词');
  }catch(e){toast(e.message)}
}

async function doCreateRoom(){
  const name=$('roomName').value.trim();
  if(!name){toast('请输入议题名称');return}
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/rooms',{name,admin_password:S.adminPwd});
    const d=await api('GET','/meeting/api/trips/'+S.tripId);
    S.tripData=d;renderTrip();toast('议题已创建');
  }catch(e){toast(e.message)}
}

async function enterRoom(roomId){
  try{
    const d=await api('GET','/meeting/api/rooms/'+roomId);
    S.roomId=roomId;S.roomData=d;
    renderRoom();
  }catch(e){toast(e.message)}
}

let ws=null;
function connectWS(roomId){
  if(ws)ws.close();
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(proto+'://'+location.host+'/meeting/ws/'+roomId);
  ws.onmessage=e=>{
    const msg=JSON.parse(e.data);
    if(msg.type==='new_message')appendMsg(msg.message);
    else if(msg.type==='summary')showSummary(msg.summary);
    else if(msg.type==='top5')showTop5(msg.top5,msg.votes);
    else if(msg.type==='votes_update')updateVotes(msg.votes);
    else if(msg.type==='message_recalled')removeMsg(msg.message_id);
  };
  ws.onclose=()=>{setTimeout(()=>{if(S.roomId===roomId)connectWS(roomId)},2000)};
}

function msgHtml(m){
  const isMine=m.author===S.userName;
  const recallBtn=isMine?`<button onclick="doRecall('${m.id}',this)" style="float:right;background:none;border:none;font-size:13px;color:#ff3b30;cursor:pointer;padding:0 0 0 8px;font-weight:600">撤回修改</button>`:'';
  return `<div class="msg-item" id="msg-${m.id}"><div class="msg-author">${m.author}${recallBtn}</div><div class="msg-content">${m.content}</div></div>`;
}

async function doRecall(msgId, btn){
  if(!S.userName)return;
  // 先存内容，API 返回前 WebSocket 可能已经删掉 DOM
  const msgEl=document.getElementById('msg-'+msgId);
  const content=msgEl?msgEl.querySelector('.msg-content').textContent:'';
  try{
    await api('POST','/meeting/api/rooms/'+S.roomId+'/messages/'+msgId+'/recall',{author:S.userName});
    const input=$('msgInput');
    if(input&&content){input.value=content;autoResize(input);input.focus();}
  }catch(e){toast(e.message)}
}

function removeMsg(msgId){
  const el=document.getElementById('msg-'+msgId);
  if(el)el.remove();
  if(S.roomData)S.roomData.messages=S.roomData.messages.filter(m=>m.id!==msgId);
  const wall=$('msgWall');
  if(wall&&!wall.querySelector('.msg-item'))wall.innerHTML='<div class="empty">还没有发言，第一个说点什么吧</div>';
}

function renderRoom(){
  const r=S.roomData;
  const isAdmin=!!S.adminPwd;
  const msgsHtml=r.messages.length?r.messages.map(msgHtml).join(''):'<div class="empty">还没有发言，第一个说点什么吧</div>';
  const adminHtml=isAdmin?`
    <div class="admin-panel"><h3>管理员操作</h3>
      <div class="admin-row">
        <button class="btn btn-secondary btn-sm" onclick="doSummarize()">AI总结</button>
        <button class="btn btn-secondary btn-sm" onclick="doRank()">评选精华发言</button>
      </div>
      <div style="margin-top:12px"><div class="label">AI提示词（仅本议题）</div>
      <textarea id="promptEdit" rows="5" style="margin-top:6px"></textarea>
      <div style="margin-top:6px;display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="doSavePrompt()">保存提示词</button>
        <button class="btn btn-secondary btn-sm" onclick="doResetPrompt()">恢复默认</button>
      </div></div>
    </div>`:'';
  app().innerHTML=`
<div class="header">
  <button class="back-btn" onclick="backToTrip()">‹</button>
  <h1>${r.name}</h1>
</div>
<div class="container">
  <div class="card">
    <div class="msg-wall" id="msgWall">${msgsHtml}</div>
    <div class="msg-row">
      <textarea class="msg-input" id="msgInput" placeholder="输入发言..." rows="1" style="resize:none;overflow:hidden;line-height:1.5;padding-top:10px;padding-bottom:10px" oninput="autoResize(this)"></textarea>
      <button class="send-btn" onclick="doSend()">发送</button>
    </div>
  </div>
  <div id="summaryBox">${r.summary?`<div class="card"><h2>AI总结</h2><div class="summary-box">${r.summary}</div></div>`:''}</div>
  <div id="top5Box">${r.top5.length?`<div class="card">${voteHeader(r.votes)}${renderTop5Html(r.top5,r.votes)}</div>`:''}</div>
  ${adminHtml}
</div>`;
  scrollMsgs();
  connectWS(S.roomId);
  if(S.adminPwd)loadPrompt();
}

function autoResize(el){el.style.height='auto';el.style.height=el.scrollHeight+'px'}

function scrollMsgs(){const w=$('msgWall');if(w)w.scrollTop=w.scrollHeight}

function appendMsg(m){
  const w=$('msgWall');
  if(!w)return;
  const empty=w.querySelector('.empty');
  if(empty)empty.remove();
  const div=document.createElement('div');
  div.innerHTML=msgHtml(m);
  w.appendChild(div.firstChild);
  scrollMsgs();
}

function showSummary(text){
  const box=$('summaryBox');
  if(box)box.innerHTML=`<div class="card"><h2>AI总结</h2><div class="summary-box">${text}</div></div>`;
}

function renderTop5Html(top5,votes){
  const medals=['🥇','🥈','🥉'];
  // 按票数排序（票数相同保持原顺序）
  const sorted=[...top5].map(item=>({...item,_votes:(votes[item.id]||[]).length}))
    .sort((a,b)=>b._votes-a._votes);
  const hasVotes=sorted.some(item=>item._votes>0);
  return sorted.map((item,i)=>{
    const vlist=votes[item.id]||[];
    const voted=vlist.includes(S.userName);
    const rank=hasVotes?(medals[i]||`<span style="font-size:15px;font-weight:700;color:#888">${i+1}</span>`):'';
    return `<div class="top5-item">
      <div class="top5-rank" style="min-width:28px">${rank}</div>
      <div class="top5-body">
        <div class="top5-content"><b>${item.author||''}</b>：${item.content||item.id}</div>
        <div class="top5-reason">${item.reason}</div>
        <div style="margin-top:6px;display:flex;align-items:center;gap:10px">
          <button class="vote-btn${voted?' voted':''}" onclick="doVote('${item.id}')">${voted?'已投票':'投票'}</button>
          ${item._votes>0?`<span style="font-size:16px;font-weight:700;color:#007aff">${item._votes} 票</span>`:'<span class="vote-count">0 票</span>'}
        </div>
      </div>
    </div>`;
  }).join('');
}

function voteHeader(votes){
  const used=S.userName?Object.values(votes).filter(v=>v.includes(S.userName)).length:0;
  const left=3-used;
  return `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <h2 style="font-size:16px;font-weight:600">精华发言投票</h2>
    <span style="font-size:13px;color:${left>0?'#007aff':'#aaa'}">${left > 0 ? `剩余 ${left} 票` : '已投完'}</span>
  </div>`;
}

function showTop5(top5,votes){
  S.roomData.top5=top5;S.roomData.votes=votes;
  const box=$('top5Box');
  if(box)box.innerHTML=`<div class="card">${voteHeader(votes)}${renderTop5Html(top5,votes)}</div>`;
}

function updateVotes(votes){
  S.roomData.votes=votes;
  const box=$('top5Box');
  if(box&&S.roomData.top5.length)box.innerHTML=`<div class="card">${voteHeader(votes)}${renderTop5Html(S.roomData.top5,votes)}</div>`;
}

async function doSend(){
  const input=$('msgInput');
  const content=input.value.trim();
  if(!content)return;
  if(!S.userName){toast('请先输入名字');return}
  input.value='';
  autoResize(input);
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/messages',{author:S.userName,content})}
  catch(e){toast(e.message);input.value=content;autoResize(input)}
}

async function doSummarize(){
  toast('AI总结中，请稍候...',8000);
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/summarize?admin_password='+encodeURIComponent(S.adminPwd))}
  catch(e){toast(e.message)}
}

async function doRank(){
  toast('AI评选中，请稍候...',8000);
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/rank?admin_password='+encodeURIComponent(S.adminPwd))}
  catch(e){toast(e.message)}
}

async function doVote(msgId){
  if(!S.userName){toast('请先输入名字');return}
  try{
    await api('POST','/meeting/api/rooms/'+S.roomId+'/vote',{voter:S.userName,message_id:msgId});
  }catch(e){toast(e.message)}
}

function myVoteCount(){
  if(!S.roomData||!S.roomData.votes)return 0;
  return Object.values(S.roomData.votes).filter(v=>v.includes(S.userName)).length;
}

async function backToTrip(){
  if(ws){ws.close();ws=null}
  S.roomId=null;S.roomData=null;
  try{S.tripData=await api('GET','/meeting/api/trips/'+S.tripId)}catch(e){}
  renderTrip();
}

// Route by path: /m = participant, /meeting = host
(function init(){
  const p=new URLSearchParams(location.search);
  const trip=p.get('trip');
  if(location.pathname==='/m'||location.pathname.startsWith('/m/')){
    renderJoin(trip);
  }else{
    renderHome();
  }
})();
</script>
</body>
</html>"""


@app.get("/meeting", response_class=HTMLResponse)
@app.get("/meeting/", response_class=HTMLResponse)
@app.get("/m", response_class=HTMLResponse)
@app.get("/m/", response_class=HTMLResponse)
async def meeting_home():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)