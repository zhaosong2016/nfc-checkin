import json
import os
import time
import uuid
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")

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
    return {"id": trip["id"], "name": trip["name"], "schedule": _normalize_schedule(trip.get("schedule")), "rooms": room_list}


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
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
const S={view:'home',tripId:null,roomId:null,userName:null,adminPwd:null,tripData:null,roomData:null,tripTab:'schedule'};

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
    if(saved){S.userName=saved;renderTrip();return}
    app().innerHTML=`
<div class="header"><h1>${d.name}</h1></div>
<div class="container">
  <div class="card">
    <h2>加入会议</h2>
    <div class="label">你的名字</div>
    <input id="joinName" placeholder="输入你的名字" autofocus>
    <div class="gap"><button class="btn btn-primary" onclick="doJoinByName()">进入</button></div>
  </div>
</div>`;
  }).catch(()=>{
    app().innerHTML=`<div class="header"><h1>前哨游学 OS</h1></div><div class="container"><div class="card"><div class="empty">行程不存在或已结束</div></div></div>`;
  });
}

async function doJoinByName(){
  const name=$('joinName').value.trim();
  if(!name){toast('请输入你的名字');return}
  S.userName=name;
  localStorage.setItem('meeting_name_'+S.tripId,name);
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
  </div>
  ${tab==='schedule'?scheduleHtml:meetingHtml}
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