import json
import time
import uuid
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

trips = {}
rooms = {}

CLAUDE_API_KEY = "sk-acw-ab368e23-755e2f50282649ed"
CLAUDE_BASE_URL = "https://api.aicodewith.com"
CLAUDE_MODEL = "claude-opus-4-7"

DEFAULT_PROMPT = (
    "你是一位专业的会议记录员。请对以下会议讨论内容进行总结：\n\n"
    "1. **核心议题**：本次讨论的主要话题\n"
    "2. **关键观点**：参与者提出的重要观点（3-5条）\n"
    "3. **精彩问题**：值得深思的问题\n"
    "4. **行动建议**：可以落地的建议或下一步行动\n\n"
    "请用简洁清晰的中文输出，突出重点。"
)

RANK_PROMPT = (
    "请从以下会议发言中，选出最有价值的5条（可以是问题、观点或建议）。\n"
    "评选标准：思考深度、创新性、实用性、启发性。\n\n"
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


class UpdatePromptReq(BaseModel):
    admin_password: str
    prompt: str


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
        "rooms": [],
        "created_at": time.time(),
    }
    return {"trip_id": trip_id}


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
    return {"id": trip["id"], "name": trip["name"], "rooms": room_list}


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
        "messages": [],
        "summary": None,
        "top5": [],
        "votes": {},
        "created_at": time.time(),
    }
    trip["rooms"].append(room_id)
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
    await manager.broadcast(room_id, {"type": "new_message", "message": msg})
    return msg


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
    msgs_text = "\n".join(f"[{m['author']}]: {m['content']}" for m in room["messages"])
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{CLAUDE_BASE_URL}/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01"},
            json={"model": CLAUDE_MODEL, "max_tokens": 2000,
                  "messages": [{"role": "user", "content": f"{trip['ai_prompt']}\n\n---会议发言---\n{msgs_text}"}]},
        )
    if resp.status_code != 200:
        raise HTTPException(500, f"AI调用失败: {resp.text[:200]}")
    summary = resp.json()["content"][0]["text"]
    room["summary"] = summary
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
        top5 = json.loads(resp.json()["content"][0]["text"]).get("top5", [])
    except Exception:
        raise HTTPException(500, "AI返回格式错误")
    room["top5"] = top5
    room["votes"] = {item["id"]: set() for item in top5}
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
    for voters in room["votes"].values():
        voters.discard(req.voter)
    room["votes"][req.message_id].add(req.voter)
    votes_out = {k: list(v) for k, v in room["votes"].items()}
    await manager.broadcast(room_id, {"type": "votes_update", "votes": votes_out})
    return {"ok": True}


@app.get("/meeting/api/trips/{trip_id}/prompt")
async def get_prompt(trip_id: str, admin_password: str):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    if trip["admin_password"] != admin_password:
        raise HTTPException(403, "密码错误")
    return {"prompt": trip["ai_prompt"]}


@app.post("/meeting/api/trips/{trip_id}/prompt")
async def update_prompt(trip_id: str, req: UpdatePromptReq):
    trip = trips.get(trip_id)
    if not trip:
        raise HTTPException(404, "行程不存在")
    if trip["admin_password"] != req.admin_password:
        raise HTTPException(403, "密码错误")
    trip["ai_prompt"] = req.prompt
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
<title>游学会议</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#f5f5f7;color:#1a1a1a;min-height:100vh}
.header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#fff;padding:16px 20px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
.header h1{font-size:18px;font-weight:600}
.back-btn{background:none;border:none;color:#fff;font-size:20px;cursor:pointer;padding:4px 8px;border-radius:6px}
.back-btn:active{background:rgba(255,255,255,.15)}
.container{max-width:600px;margin:0 auto;padding:20px 16px}
.card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card h2{font-size:16px;font-weight:600;margin-bottom:14px;color:#1a1a1a}
input,textarea{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:15px;font-family:inherit;outline:none;transition:border-color .2s;background:#fafafa}
input:focus,textarea:focus{border-color:#007aff;background:#fff}
textarea{resize:vertical;min-height:80px}
.btn{display:block;width:100%;padding:14px;border:none;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer;transition:opacity .15s,transform .1s}
.btn:active{opacity:.85;transform:scale(.98)}
.btn-primary{background:#007aff;color:#fff}
.btn-secondary{background:#f0f0f5;color:#1a1a1a}
.btn-danger{background:#ff3b30;color:#fff}
.btn-sm{padding:8px 16px;font-size:14px;width:auto;display:inline-block}
.btn-green{background:#34c759;color:#fff}
.label{font-size:13px;color:#666;margin-bottom:6px;font-weight:500}
.gap{margin-top:12px}
.room-card{background:#fff;border-radius:12px;padding:16px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.07);display:flex;align-items:center;justify-content:space-between;cursor:pointer;transition:background .15s}
.room-card:active{background:#f0f0f5}
.room-name{font-size:16px;font-weight:600}
.room-meta{font-size:13px;color:#888;margin-top:2px}
.arrow{color:#c7c7cc;font-size:18px}
.msg-wall{background:#f5f5f7;border-radius:10px;padding:12px;min-height:200px;max-height:50vh;overflow-y:auto;margin-bottom:12px}
.msg-item{background:#fff;border-radius:10px;padding:10px 12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.msg-author{font-size:12px;color:#888;margin-bottom:3px}
.msg-content{font-size:15px;line-height:1.5}
.msg-row{display:flex;gap:8px;align-items:flex-end}
.msg-input{flex:1}
.send-btn{background:#007aff;color:#fff;border:none;border-radius:10px;padding:12px 18px;font-size:15px;font-weight:600;cursor:pointer;white-space:nowrap}
.send-btn:active{opacity:.8}
.divider{height:1px;background:#e8e8ed;margin:16px 0}
.summary-box{background:#f0f7ff;border-radius:10px;padding:14px;font-size:14px;line-height:1.7;white-space:pre-wrap;color:#1a1a1a}
.top5-item{background:#fff;border-radius:10px;padding:12px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.06);display:flex;gap:10px;align-items:flex-start}
.top5-rank{font-size:20px;min-width:28px}
.top5-body{flex:1}
.top5-content{font-size:14px;line-height:1.5;margin-bottom:4px}
.top5-reason{font-size:12px;color:#888}
.vote-btn{background:#f0f0f5;border:none;border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer;font-weight:600}
.vote-btn.voted{background:#007aff;color:#fff}
.vote-count{font-size:12px;color:#888;margin-left:6px}
.admin-panel{background:#fff8f0;border-radius:12px;padding:16px;margin-top:16px}
.admin-panel h3{font-size:14px;color:#ff9500;font-weight:600;margin-bottom:12px}
.admin-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tag{display:inline-block;background:#e8f4ff;color:#007aff;border-radius:6px;padding:3px 8px;font-size:12px;font-weight:600}
.toast{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#1a1a1a;color:#fff;padding:10px 20px;border-radius:20px;font-size:14px;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.loading{text-align:center;color:#888;padding:20px;font-size:14px}
.empty{text-align:center;color:#aaa;padding:30px;font-size:14px}
.qr-box{text-align:center;padding:10px 0}
.qr-box img{width:180px;height:180px;border-radius:10px}
.qr-url{font-size:12px;color:#888;margin-top:8px;word-break:break-all}
.admin-toggle{font-size:13px;color:#007aff;cursor:pointer;text-align:right;margin-top:8px}
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
const S={view:'home',tripId:null,roomId:null,userName:null,adminPwd:null,tripData:null,roomData:null};

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

function renderHome(){
  app().innerHTML=`
<div class="header"><h1>游学会议</h1></div>
<div class="container">
  <div class="card">
    <h2>加入行程</h2>
    <div class="label">行程码</div>
    <input id="joinCode" placeholder="输入8位行程码" maxlength="8">
    <div class="gap"><div class="label">你的名字</div>
    <input id="joinName" placeholder="输入你的名字"></div>
    <div class="gap"><button class="btn btn-primary" onclick="doJoin()">进入行程</button></div>
  </div>
  <div class="card">
    <h2>创建行程（主持人）</h2>
    <div class="label">行程名称</div>
    <input id="tripName" placeholder="如：硅谷游学2026">
    <div class="gap"><div class="label">管理员密码</div>
    <input id="tripPwd" type="password" placeholder="设置密码，用于管理员操作"></div>
    <div class="gap"><button class="btn btn-secondary" onclick="doCreate()">创建行程</button></div>
  </div>
</div>`;
}

async function doJoin(){
  const code=$('joinCode').value.trim();
  const name=$('joinName').value.trim();
  if(!code||!name){toast('请填写行程码和名字');return}
  try{
    const d=await api('GET','/meeting/api/trips/'+code);
    S.tripId=code;S.userName=name;S.tripData=d;
    localStorage.setItem('meeting_name_'+code,name);
    renderTrip();
  }catch(e){toast(e.message)}
}

async function doCreate(){
  const name=$('tripName').value.trim();
  const pwd=$('tripPwd').value.trim();
  if(!name||!pwd){toast('请填写行程名称和密码');return}
  try{
    const d=await api('POST','/meeting/api/trips',{name,admin_password:pwd});
    S.tripId=d.trip_id;S.adminPwd=pwd;S.userName='主持人';
    const td=await api('GET','/meeting/api/trips/'+d.trip_id);
    S.tripData=td;
    renderTrip();
  }catch(e){toast(e.message)}
}

function renderTrip(){
  const t=S.tripData;
  const isAdmin=!!S.adminPwd;
  const joinUrl=location.origin+'/meeting?trip='+S.tripId;
  const qrUrl='https://api.qrserver.com/v1/create-qr-code/?size=180x180&data='+encodeURIComponent(joinUrl);
  let roomsHtml=t.rooms.length?t.rooms.map(r=>`
    <div class="room-card" onclick="enterRoom('${r.id}')">
      <div><div class="room-name">${r.name}</div><div class="room-meta">${r.msg_count} 条发言</div></div>
      <div class="arrow">›</div>
    </div>`).join(''):'<div class="empty">暂无会议室</div>';
  let adminHtml=isAdmin?`
    <div class="card"><h2>创建会议室</h2>
      <div class="label">会议室名称</div>
      <input id="roomName" placeholder="如：主会场、分组A">
      <div class="gap"><button class="btn btn-secondary btn-sm" onclick="doCreateRoom()">创建</button></div>
    </div>
    <div class="card"><h2>AI提示词</h2>
      <div class="label">总结提示词（管理员可修改）</div>
      <textarea id="promptEdit" rows="6"></textarea>
      <div class="gap"><button class="btn btn-secondary btn-sm" onclick="doSavePrompt()">保存提示词</button></div>
    </div>`:'';
  app().innerHTML=`
<div class="header">
  <button class="back-btn" onclick="renderHome()">‹</button>
  <h1>${t.name}</h1>
  ${isAdmin?'<span class="tag" style="margin-left:auto">管理员</span>':''}
</div>
<div class="container">
  <div class="card"><h2>扫码加入</h2>
    <div class="qr-box"><img src="${qrUrl}" alt="QR"><div class="qr-url">行程码：<b>${S.tripId}</b></div></div>
  </div>
  <div class="card"><h2>会议室列表</h2>${roomsHtml}</div>
  ${adminHtml}
  ${!isAdmin?'<div class="admin-toggle" onclick="showAdminLogin()">管理员登录</div>':''}
</div>`;
  if(isAdmin)loadPrompt();
}

function showAdminLogin(){
  const pwd=prompt('请输入管理员密码');
  if(!pwd)return;
  api('GET','/meeting/api/trips/'+S.tripId+'/prompt?admin_password='+encodeURIComponent(pwd))
    .then(()=>{S.adminPwd=pwd;toast('管理员模式已开启');renderTrip()})
    .catch(()=>{toast('密码错误')});
}

async function loadPrompt(){
  try{
    const d=await api('GET','/meeting/api/trips/'+S.tripId+'/prompt?admin_password='+encodeURIComponent(S.adminPwd));
    if($('promptEdit'))$('promptEdit').value=d.prompt;
  }catch(e){}
}

async function doSavePrompt(){
  const p=$('promptEdit').value.trim();
  if(!p){toast('提示词不能为空');return}
  try{await api('POST','/meeting/api/trips/'+S.tripId+'/prompt',{admin_password:S.adminPwd,prompt:p});toast('提示词已保存')}
  catch(e){toast(e.message)}
}

async function doCreateRoom(){
  const name=$('roomName').value.trim();
  if(!name){toast('请输入会议室名称');return}
  try{
    await api('POST','/meeting/api/trips/'+S.tripId+'/rooms',{name,admin_password:S.adminPwd});
    const d=await api('GET','/meeting/api/trips/'+S.tripId);
    S.tripData=d;renderTrip();toast('会议室已创建');
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
  };
  ws.onclose=()=>{setTimeout(()=>{if(S.roomId===roomId)connectWS(roomId)},2000)};
}

function msgHtml(m){
  return `<div class="msg-item"><div class="msg-author">${m.author}</div><div class="msg-content">${m.content}</div></div>`;
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
      <input class="msg-input" id="msgInput" placeholder="输入发言..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();doSend()}">
      <button class="send-btn" onclick="doSend()">发送</button>
    </div>
  </div>
  <div id="summaryBox">${r.summary?`<div class="card"><h2>AI总结</h2><div class="summary-box">${r.summary}</div></div>`:''}</div>
  <div id="top5Box">${r.top5.length?`<div class="card"><h2>精华发言投票</h2>${renderTop5Html(r.top5,r.votes)}</div>`:''}</div>
  ${adminHtml}
</div>`;
  scrollMsgs();
  connectWS(S.roomId);
}

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
  const ranks=['🥇','🥈','🥉','4️⃣','5️⃣'];
  return top5.map((item,i)=>{
    const msg=S.roomData.messages.find(m=>m.id===item.id)||{author:'',content:item.id};
    const vlist=votes[item.id]||[];
    const voted=vlist.includes(S.userName);
    return `<div class="top5-item">
      <div class="top5-rank">${ranks[i]}</div>
      <div class="top5-body">
        <div class="top5-content"><b>${msg.author}</b>：${msg.content}</div>
        <div class="top5-reason">${item.reason}</div>
        <div style="margin-top:6px">
          <button class="vote-btn${voted?' voted':''}" onclick="doVote('${item.id}')">${voted?'已投票':'投票'}</button>
          <span class="vote-count">${vlist.length} 票</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

function showTop5(top5,votes){
  S.roomData.top5=top5;S.roomData.votes=votes;
  const box=$('top5Box');
  if(box)box.innerHTML=`<div class="card"><h2>精华发言投票</h2>${renderTop5Html(top5,votes)}</div>`;
}

function updateVotes(votes){
  S.roomData.votes=votes;
  const box=$('top5Box');
  if(box&&S.roomData.top5.length)box.innerHTML=`<div class="card"><h2>精华发言投票</h2>${renderTop5Html(S.roomData.top5,votes)}</div>`;
}

async function doSend(){
  const input=$('msgInput');
  const content=input.value.trim();
  if(!content)return;
  if(!S.userName){toast('请先输入名字');return}
  input.value='';
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/messages',{author:S.userName,content})}
  catch(e){toast(e.message);input.value=content}
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
  try{await api('POST','/meeting/api/rooms/'+S.roomId+'/vote',{voter:S.userName,message_id:msgId})}
  catch(e){toast(e.message)}
}

async function backToTrip(){
  if(ws){ws.close();ws=null}
  S.roomId=null;S.roomData=null;
  try{S.tripData=await api('GET','/meeting/api/trips/'+S.tripId)}catch(e){}
  renderTrip();
}

// Handle ?trip= param on load
(function init(){
  const p=new URLSearchParams(location.search);
  const trip=p.get('trip');
  if(trip){
    S.tripId=trip;
    const saved=localStorage.getItem('meeting_name_'+trip);
    if(saved){
      S.userName=saved;
      api('GET','/meeting/api/trips/'+trip).then(d=>{S.tripData=d;renderTrip()}).catch(()=>renderHome());
    }else{
      renderHome();
      setTimeout(()=>{if($('joinCode'))$('joinCode').value=trip},100);
    }
  }else{renderHome()}
})();
</script>
</body>
</html>"""


@app.get("/meeting", response_class=HTMLResponse)
@app.get("/meeting/", response_class=HTMLResponse)
async def meeting_home():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)