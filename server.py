# -*- coding: utf-8 -*-
"""
NFC v5.1 - 修复实时推送
"""
import asyncio,csv,json,time,threading,os,sys
sys.stdout=os.fdopen(sys.stdout.fileno(),'w',buffering=1)
from datetime import datetime
from pathlib import Path

CONFIG={"host":"0.0.0.0","port":8000,"roster_file":"roster.csv","checkin_log":"checkin_log.csv","poll_interval":0.3}
CLOUD_URL=os.environ.get("CLOUD_URL","")  # e.g. https://myspaceone.com/siliconvalley
CLOUD_SECRET=os.environ.get("CLOUD_SECRET","nfc2026")
roster=[];ws_clients=set();main_loop=None

def push_to_cloud(event):
    if not CLOUD_URL:return
    def _send():
        try:
            import urllib.request
            data=json.dumps(event,ensure_ascii=False).encode()
            req=urllib.request.Request(CLOUD_URL.rstrip("/")+"/api/push",data=data,
                headers={"Content-Type":"application/json","X-Secret":CLOUD_SECRET})
            urllib.request.urlopen(req,timeout=5)
        except Exception as e:print(f"[CLOUD] 推送失败: {e}")
    threading.Thread(target=_send,daemon=True).start()

def load_roster():
    global roster;path=Path(CONFIG["roster_file"])
    if not path.exists():create_sample_roster()
    roster=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for i,row in enumerate(csv.DictReader(f)):
            roster.append({"id":i,"name":row.get("name",f"学员{i+1}"),"uid":row.get("uid","").strip().upper(),"phone":row.get("phone",""),"bus":int(row.get("bus","1") or "1"),"checkedIn":False,"time":None})
    print(f"[INFO] 已加载 {len(roster)} 人名单")

def create_sample_roster():
    with open(CONFIG["roster_file"],"w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["name","uid","phone","bus"])
        nm="张伟,李娜,王强,刘洋,陈静,杨磊,赵敏,黄海,周芳,吴涛,徐明,孙悦,胡博,朱雅,高飞,林慧,何鑫,郭欣,马超,罗平,梁宇,宋婷,郑刚,谢瑶,韩旭,唐睿,冯思,于晨,董萌,萧阳,程峰,曹雨,袁翔,邓佳,许乐,傅琪,沈昊,曾诗,彭志,吕建,苏梦,卢岩,蒋华,蔡文,贾鹏,丁霞,魏国,薛勇,叶军,阎杰,余欣怡,潘天宇,杜雅琪,戴浩然,夏诗涵,钟博文,汪嘉欣,田睿阳,任梦瑶,姜志远,范雨萱,方俊杰,石佳慧,姚宇航,谭思琪,廖浩宇,邹心怡,熊明哲,金雅婷,陆天翔,郝子涵,孔思远,白雨桐,崔晨曦,康若溪,毛嘉豪,邱紫萱,秦逸飞,江诗语,尹浩铭,钱思颖,龙峻熙,万梓萌,段子墨,雷可欣,侯文博,龚雨彤,邵明轩,洪思瑜,贺子豪,文雅馨,常博远,温可馨,武逸辰,柳思琦,施皓宇,顾梦涵,牛嘉琪,尚子轩,樊悦然".split(",")
        for i,n in enumerate(nm):w.writerow([n,"",f"1380000{1001+i}",1 if i<53 else 2])

def save_roster():
    with open(CONFIG["roster_file"],"w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["name","uid","phone","bus"])
        for p in roster:w.writerow([p["name"],p["uid"],p.get("phone",""),p.get("bus",1)])

def log_checkin(person):
    with open(CONFIG["checkin_log"],"a",encoding="utf-8-sig",newline="") as f:
        csv.writer(f).writerow([person["name"],person["uid"],person["time"],datetime.now().strftime("%Y-%m-%d"),person.get("bus",1)])

def find_person_by_uid(uid):
    uid=uid.upper().strip()
    for p in roster:
        if p["uid"]==uid:return p
    return None

class NFCReader:
    def __init__(self):
        self.reader=None;self.available=False;self.last_uid=None;self.last_read_time=0
    def _init(self):
        for attempt in range(5):
            try:
                from smartcard.System import readers;rl=readers()
                if not rl:print("[WARN] 未检测到NFC读卡器");return
                self.reader=rl[0];print(f"[INFO] 已连接读卡器: {self.reader}");self.available=True;return
            except ImportError:print("[WARN] 未安装pyscard，演示模式");return
            except Exception as e:
                import traceback;traceback.print_exc()
                if attempt<4:time.sleep(1)
                else:print(f"[WARN] 读卡器初始化失败: {e}")
    def read_uid(self):
        if not self.available:return None
        try:
            from smartcard.util import toHexString
            c=self.reader.createConnection();c.connect()
            data,sw1,sw2=c.transmit([0xFF,0xCA,0x00,0x00,0x00])
            try:c.disconnect()
            except:pass
            if sw1==0x90 and sw2==0x00:
                uid=toHexString(data).replace(" ",":");now=time.time()
                if uid==self.last_uid and(now-self.last_read_time)<2.0:return None
                self.last_uid=uid;self.last_read_time=now;return uid
            return None
        except:return None

async def broadcast(msg):
    if ws_clients:
        d=json.dumps(msg,ensure_ascii=False)
        dead=set()
        for c in ws_clients:
            try:await c.send_text(d)
            except:dead.add(c)
        ws_clients.difference_update(dead)

def safe_broadcast(msg):
    global main_loop
    if main_loop is None:return
    asyncio.run_coroutine_threadsafe(broadcast(msg),main_loop)

def nfc_poll_loop(reader):
    print("[INFO] NFC轮询已启动")
    err_count=0
    while True:
        try:
            uid=reader.read_uid()
            err_count=0
        except Exception as e:
            err_count+=1
            if err_count<=3:print(f"[WARN] NFC读取异常: {e}")
            time.sleep(1);continue
        if uid:
            print(f"[NFC] 读取到卡片: {uid}")
            person=find_person_by_uid(uid)
            if person:
                if not person["checkedIn"]:
                    person["checkedIn"]=True;person["time"]=datetime.now().strftime("%H:%M:%S");log_checkin(person)
                    print(f"[签到] {person['name']} 签到成功 ({person['time']})")
                    ev={"type":"checkin","id":person["id"],"name":person["name"],"time":person["time"],"bus":person.get("bus",1),"phone":person.get("phone","")}
                    safe_broadcast(ev);push_to_cloud(ev)
                else:
                    print(f"[签到] {person['name']} 已签到，忽略")
                    safe_broadcast({"type":"duplicate","id":person["id"],"name":person["name"]})
            else:
                print(f"[WARN] 未知卡片 UID: {uid}")
                safe_broadcast({"type":"unknown","uid":uid})
        time.sleep(CONFIG["poll_interval"])

def create_app(reader):
    from fastapi import FastAPI,WebSocket,WebSocketDisconnect,Request
    from fastapi.responses import HTMLResponse,JSONResponse
    app=FastAPI(title="NFC签到")
    @app.on_event("startup")
    async def startup():
        global main_loop
        main_loop=asyncio.get_running_loop()
        print("[INFO] 主事件循环已就绪")
        reader._init()
        if reader.available:
            threading.Thread(target=nfc_poll_loop,args=(reader,),daemon=True).start()
        else:print("[INFO] 无读卡器，演示模式")
    @app.get("/")
    async def index():return HTMLResponse(HTML)
    @app.websocket("/ws")
    async def ws_ep(ws:WebSocket):
        await ws.accept();ws_clients.add(ws)
        print(f"[WS] 客户端已连接 ({len(ws_clients)})")
        await ws.send_json({"type":"roster","roster":roster})
        try:
            while True:
                data=await ws.receive_text();msg=json.loads(data)
                if msg.get("type")=="manual_checkin":
                    pid=msg.get("id")
                    if 0<=pid<len(roster):
                        p=roster[pid]
                        if not p["checkedIn"]:
                            p["checkedIn"]=True;p["time"]=datetime.now().strftime("%H:%M:%S");log_checkin(p)
                            ev={"type":"checkin","id":p["id"],"name":p["name"],"time":p["time"],"bus":p.get("bus",1),"phone":p.get("phone","")}
                            await broadcast(ev);push_to_cloud(ev)
                elif msg.get("type")=="undo_checkin":
                    pid=msg.get("id")
                    for p in roster:
                        if p["id"]==pid:p["checkedIn"]=False;p["time"]=None
                    await broadcast({"type":"roster","roster":roster})
                    push_to_cloud({"type":"undo_checkin","id":pid})
                elif msg.get("type")=="reset_bus":
                    bus=msg.get("bus",1)
                    for p in roster:
                        if p.get("bus",1)==bus:p["checkedIn"]=False;p["time"]=None
                    await broadcast({"type":"roster","roster":roster})
                    push_to_cloud({"type":"roster_reset"})
                elif msg.get("type")=="add_temp":
                    name=msg.get("name","").strip();bus=msg.get("bus",1)
                    if name:
                        new_id=max([p["id"]for p in roster],default=-1)+1
                        roster.append({"id":new_id,"name":name,"uid":"","phone":"","bus":bus,"checkedIn":False,"time":None})
                        save_roster();await broadcast({"type":"roster","roster":roster})
                elif msg.get("type")=="remove_person":
                    pid=msg.get("id")
                    roster[:]=[p for p in roster if p["id"]!=pid]
                    save_roster();await broadcast({"type":"roster","roster":roster})
        except WebSocketDisconnect:ws_clients.discard(ws);print(f"[WS] 断开 ({len(ws_clients)})")
    @app.get("/api/roster")
    async def get_roster():return JSONResponse(roster)
    @app.post("/api/push")
    async def receive_push(request:Request):
        if request.headers.get("X-Secret")!=CLOUD_SECRET:
            return JSONResponse({"error":"forbidden"},status_code=403)
        msg=await request.json()
        if msg.get("type")=="checkin":
            pid=msg.get("id")
            p=next((x for x in roster if x["id"]==pid),None)
            if p and not p["checkedIn"]:
                p["checkedIn"]=True;p["time"]=msg.get("time","")
                await broadcast(msg)
        elif msg.get("type")=="roster_reset":
            for p in roster:p["checkedIn"]=False;p["time"]=None
            await broadcast({"type":"roster","roster":roster})
        elif msg.get("type")=="undo_checkin":
            pid=msg.get("id")
            for p in roster:
                if p["id"]==pid:p["checkedIn"]=False;p["time"]=None
            await broadcast({"type":"roster","roster":roster})
        return JSONResponse({"ok":True})
    return app

HTML=r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no"><title>NFC 签到</title><style>*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}::-webkit-scrollbar{display:none}body{font-family:'PingFang SC','Noto Sans SC',-apple-system,sans-serif;background:#f5f5f7;color:#1a1a1a;min-height:100vh;max-width:500px;margin:0 auto}input{font-family:inherit}@keyframes slideDown{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:translateY(0)}}@keyframes popToast{from{opacity:0;transform:translateX(-50%) translateY(-10px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}@keyframes celebrateBg{0%,100%{background-position:0% 50%}50%{background-position:100% 50%}}@keyframes pulse{0%,100%{opacity:.5}50%{opacity:1}}.row-enter{animation:slideDown .3s ease-out}.header{position:sticky;top:0;z-index:20;background:linear-gradient(180deg,#f5f5f7 90%,transparent);padding:14px 16px 12px}.toast{position:fixed;top:100px;left:50%;transform:translateX(-50%);padding:10px 24px;border-radius:10px;font-size:15px;font-weight:600;background:rgba(0,200,83,.12);border:1px solid rgba(0,200,83,.3);color:#00A854;z-index:100;animation:popToast .2s;white-space:nowrap;display:none}.toast.show{display:block}.toast.warn{background:rgba(255,152,0,.12);border-color:rgba(255,152,0,.3);color:#e65100}.modal-bg{position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;padding:20px}.modal{background:#fff;border-radius:16px;padding:24px;width:100%;max-width:340px;border:1px solid rgba(0,0,0,.08);box-shadow:0 4px 24px rgba(0,0,0,.1)}.modal h3{font-size:18px;font-weight:700;margin-bottom:8px;color:#1a1a1a}.modal .desc{font-size:14px;color:rgba(0,0,0,.45);margin-bottom:20px;line-height:1.5}.modal input{width:100%;padding:12px 14px;border-radius:10px;font-size:16px;background:#f5f5f7;border:1px solid rgba(0,0,0,.1);color:#1a1a1a;outline:none;margin-bottom:16px}.mbtn{flex:1;padding:12px 0;border-radius:10px;font-size:15px;cursor:pointer;font-family:inherit;text-align:center}.mbtn-cancel{background:#f0f0f0;border:1px solid rgba(0,0,0,.1);color:rgba(0,0,0,.4)}.mbtn-blue{background:rgba(0,176,255,.15);border:1px solid rgba(0,176,255,.3);color:#00B0FF;font-weight:600}.mbtn-red{background:rgba(255,107,107,.15);border:1px solid rgba(255,107,107,.3);color:#FF6B6B;font-weight:600}.mbtn-gold{background:rgba(255,176,0,.15);border:1px solid rgba(255,176,0,.3);color:#FFB000;font-weight:600}.overlay{position:fixed;inset:0;z-index:150;background:rgba(245,245,247,.97);display:none;flex-direction:column;align-items:center;justify-content:center;animation:slideDown .4s}.overlay.show{display:flex}@keyframes flashIn{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)}}@keyframes flashOut{from{opacity:1}to{opacity:0}}#checkin-flash{position:fixed;inset:0;z-index:300;background:#00C853;display:none;flex-direction:column;align-items:center;justify-content:center}#checkin-flash.show{display:flex;animation:flashIn .15s ease-out}#checkin-flash.hide{animation:flashOut .3s ease-in forwards}</style></head><body><div class="toast" id="toast"></div><div id="checkin-flash"><div id="flash-name" style="font-size:72px;font-weight:800;color:#fff;text-shadow:0 2px 20px rgba(0,0,0,.15);text-align:center;padding:0 20px;line-height:1.1"></div><div style="font-size:26px;color:rgba(255,255,255,.9);margin-top:16px;font-weight:600">签到成功 ✓</div></div><div id="app"></div><div class="overlay" id="overlay"><div style="font-size:72px;margin-bottom:20px">🎉</div><div style="font-size:32px;font-weight:800;background:linear-gradient(135deg,#00E676,#00B0FF,#FF6D00);background-size:200% 200%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:celebrateBg 3s ease infinite">全员到齐</div><div style="font-size:16px;color:rgba(0,0,0,.5);margin-top:8px" id="ov-sub"></div><div style="font-size:14px;color:rgba(0,0,0,.4);margin-top:4px">出发！</div><button onclick="document.getElementById('overlay').classList.remove('show')" style="margin-top:32px;padding:12px 40px;border-radius:10px;background:rgba(0,230,118,.12);border:1px solid rgba(0,230,118,.3);color:#00E676;font-size:16px;font-weight:600;cursor:pointer;font-family:inherit">关闭</button></div><script>let roster=[],ws=null,justId=null,ckOrder=0,currentBus=1,round=1,roundHistory=[],timerRunning=false,timerStart=null,timerElapsed=0,timerInterval=null,searchQuery='';function fmtTime(s){return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}function playFX(t){try{const c=new(window.AudioContext||window.webkitAudioContext)(),n=c.currentTime;if(t==='scan'){const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(880,n);o.frequency.exponentialRampToValueAtTime(1320,n+.08);g.gain.setValueAtTime(.2,n);g.gain.exponentialRampToValueAtTime(.01,n+.2);o.start(n);o.stop(n+.2)}else if(t==='complete'){[523,659,784,1047].forEach((f,i)=>{const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.value=f;g.gain.setValueAtTime(.15,n+i*.12);g.gain.exponentialRampToValueAtTime(.01,n+i*.12+.4);o.start(n+i*.12);o.stop(n+i*.12+.4)})}}catch(e){}}function toggleTimer(){if(timerRunning){timerRunning=false;clearInterval(timerInterval);timerInterval=null}else{timerStart=Date.now();timerElapsed=0;timerRunning=true;timerInterval=setInterval(()=>{timerElapsed=Math.floor((Date.now()-timerStart)/1000);const el=document.getElementById('timer-display');if(el){el.textContent=fmtTime(timerElapsed);el.style.color=timerElapsed>300?'#FF6B6B':timerElapsed>120?'#FFB000':'#00E676';el.style.animation=timerElapsed>120?'pulse 1.5s infinite':'none'}},1000)}render()}function connectWS(){try{const base=location.pathname.replace(/\/+$/,'');const proto=location.protocol==='https:'?'wss:':'ws:';ws=new WebSocket(proto+'//'+location.host+base+'/ws');ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='roster'){roster=m.roster;roster.forEach(p=>{if(!p.ckOrder)p.ckOrder=0});render()}else if(m.type==='checkin')doCheckin(m.id,m.name,m.time);else if(m.type==='duplicate')showToast(m.name+' 已签到',true);else if(m.type==='unknown')showToast('未识别卡片',true)};ws.onclose=()=>setTimeout(connectWS,3000)}catch(e){setTimeout(connectWS,3000)}}function doCheckin(id,name,time){const p=roster.find(r=>r.id===id);if(!p)return;if(p.checkedIn){showToast(p.name+' 已签到');return}ckOrder++;p.checkedIn=true;p.time=time||new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});p.ckOrder=ckOrder;justId=id;playFX('scan');showCheckinFlash(p.name);render();setTimeout(()=>{justId=null;render()},1200);const bm=roster.filter(x=>x.bus===p.bus);if(bm.every(x=>x.checkedIn)){setTimeout(()=>{document.getElementById('ov-sub').textContent=p.bus+' 车 · '+bm.length+' 人'+(timerRunning?' · 用时 '+fmtTime(timerElapsed):'');document.getElementById('overlay').classList.add('show');playFX('complete')},600)}}function showCheckinFlash(name){const f=document.getElementById('checkin-flash');document.getElementById('flash-name').textContent=name;f.className='show';clearTimeout(f._t);f._t=setTimeout(()=>{f.classList.add('hide');setTimeout(()=>{f.className=''},300)},1000)}function showToast(m,warn){const t=document.getElementById('toast');t.textContent=m;t.className='toast show'+(warn?' warn':'');setTimeout(()=>t.classList.remove('show'),2000)}function demoScan(){const u=roster.filter(r=>r.bus===currentBus&&!r.checkedIn);if(u.length)doCheckin(u[Math.floor(Math.random()*u.length)].id)}function demoClick(id){const p=roster.find(r=>r.id===id);if(!p||p.checkedIn)return;const bg=document.createElement('div');bg.className='modal-bg';bg.onclick=e=>{if(e.target===bg)bg.remove()};bg.innerHTML='<div class="modal"><h3>确认点名</h3><div class="desc">确认将 <b style="color:#1a1a1a">'+p.name+'</b> 手动签到？</div><div style="display:flex;gap:10px"><button class="mbtn mbtn-cancel" onclick="this.closest(\'.modal-bg\').remove()">取消</button><button class="mbtn mbtn-blue" onclick="confirmDemoClick('+p.id+')">确认签到</button></div></div>';document.body.appendChild(bg)}function confirmDemoClick(id){document.querySelector('.modal-bg')?.remove();const p=roster.find(r=>r.id===id);if(p&&!p.checkedIn)doCheckin(p.id,p.name)}function switchBus(b){currentBus=b;document.getElementById('overlay').classList.remove('show');searchQuery='';render()}function setSearch(v){searchQuery=v;render()}function clearSearch(){searchQuery='';render();document.getElementById('search-input')?.focus()}function showNewRoundModal(){const bg=document.createElement('div');bg.className='modal-bg';bg.id='m-round';bg.onclick=e=>{if(e.target===bg)bg.remove()};bg.innerHTML='<div class="modal"><h3>开始新一轮？</h3><div class="desc">第 '+round+' 轮（'+currentBus+' 车'+(timerRunning?'，已等待 '+fmtTime(timerElapsed):'')+'）记录将保存并重置。</div><div style="display:flex;gap:10px"><button class="mbtn mbtn-cancel" onclick="this.closest(\'.modal-bg\').remove()">取消</button><button class="mbtn mbtn-blue" onclick="startNewRound()">确认</button></div></div>';document.body.appendChild(bg)}function startNewRound(){const br=roster.filter(p=>p.bus===currentBus);roundHistory.push({round,bus:currentBus,time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),elapsed:timerRunning?timerElapsed:null,data:br.map(p=>({name:p.name,checkedIn:p.checkedIn,time:p.time,bus:p.bus}))});roster.forEach(p=>{if(p.bus===currentBus){p.checkedIn=false;p.time=null;p.ckOrder=0}});ckOrder=0;round++;timerRunning=false;timerElapsed=0;timerStart=null;if(timerInterval){clearInterval(timerInterval);timerInterval=null}document.getElementById('overlay').classList.remove('show');document.getElementById('m-round')?.remove();searchQuery='';render()}function showAddModal(){const bg=document.createElement('div');bg.className='modal-bg';bg.id='m-add';bg.onclick=e=>{if(e.target===bg)bg.remove()};bg.innerHTML='<div class="modal"><h3>临时加人</h3><div style="font-size:13px;color:rgba(0,0,0,.4);margin-bottom:12px">添加到 '+currentBus+' 车</div><input type="text" id="temp-name" placeholder="输入姓名"><div style="display:flex;gap:10px"><button class="mbtn mbtn-cancel" onclick="this.closest(\'.modal-bg\').remove()">取消</button><button class="mbtn mbtn-gold" onclick="addTemp()">添加</button></div></div>';document.body.appendChild(bg);setTimeout(()=>{const i=document.getElementById('temp-name');if(i){i.focus();i.onkeydown=e=>{if(e.key==='Enter')addTemp()}}},100)}function addTemp(){const i=document.getElementById('temp-name');if(!i)return;const n=i.value.trim();if(!n)return;roster.push({id:Math.max(...roster.map(p=>p.id),0)+1,name:n,uid:'',phone:'',bus:currentBus,checkedIn:false,time:null,ckOrder:0,isTemp:true});document.getElementById('m-add')?.remove();showToast('已添加 '+n);render()}function showRemoveModal(){const bg=document.createElement('div');bg.style.cssText='position:fixed;inset:0;z-index:200;background:rgba(245,245,247,.97);display:flex;flex-direction:column';bg.id='m-remove';const br=roster.filter(p=>p.bus===currentBus);let h='<div style="flex:1;overflow:auto;padding:20px 16px"><div style="max-width:360px;margin:0 auto"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><div style="font-size:18px;font-weight:700;color:#1a1a1a">减人（'+currentBus+'车）</div><button style="padding:6px 16px;border-radius:6px;font-size:13px;background:rgba(0,0,0,.04);border:1px solid rgba(0,0,0,.1);color:rgba(0,0,0,.4);cursor:pointer;font-family:inherit" onclick="document.getElementById(\'m-remove\').remove()">关闭</button></div>';br.forEach(p=>{h+='<div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid rgba(0,0,0,.06)"><div style="font-size:16px;color:#1a1a1a">'+p.name+(p.checkedIn?'<span style="font-size:11px;color:rgba(0,180,83,.6);margin-left:6px">已签到</span>':'')+(p.isTemp?'<span style="font-size:11px;color:#FFB000;margin-left:6px">临时</span>':'')+'</div><button onclick="confirmRemove('+p.id+',\''+p.name+'\')" style="padding:5px 12px;border-radius:6px;font-size:12px;background:rgba(255,107,107,.08);border:1px solid rgba(255,107,107,.2);color:#FF6B6B;cursor:pointer;font-family:inherit;white-space:nowrap">删除</button></div>'});h+='</div></div>';bg.innerHTML=h;document.body.appendChild(bg)}function confirmRemove(id,name){const bg=document.createElement('div');bg.className='modal-bg';bg.id='m-confirm-remove';bg.onclick=e=>{if(e.target===bg)bg.remove()};bg.innerHTML='<div class="modal"><h3>确认删除</h3><div class="desc">确认将 <b style="color:#1a1a1a">'+name+'</b> 从名单中删除？此操作不可恢复。</div><div style="display:flex;gap:10px"><button class="mbtn mbtn-cancel" onclick="this.closest(\'.modal-bg\').remove()">取消</button><button class="mbtn mbtn-red" onclick="doRemove('+id+')">确认删除</button></div></div>';document.body.appendChild(bg)}function doRemove(id){document.getElementById('m-confirm-remove')?.remove();const p=roster.find(r=>r.id===id);roster=roster.filter(r=>r.id!==id);if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'remove_person',id}));showToast((p?.name||'')+'已删除');document.getElementById('m-remove')?.remove();render()}function showUndoModal(id){const p=roster.find(r=>r.id===id);if(!p)return;const bg=document.createElement('div');bg.className='modal-bg';bg.id='m-undo';bg.onclick=e=>{if(e.target===bg)bg.remove()};bg.innerHTML='<div class="modal"><h3>撤销签到</h3><div class="desc">确认将 <b style="color:#1a1a1a">'+p.name+'</b> 撤销为未签到？</div><div style="display:flex;gap:10px"><button class="mbtn mbtn-cancel" onclick="this.closest(\'.modal-bg\').remove()">取消</button><button class="mbtn mbtn-red" onclick="doUndo('+id+')">确认撤销</button></div></div>';document.body.appendChild(bg)}function doUndo(id){const p=roster.find(r=>r.id===id);if(p){p.checkedIn=false;p.time=null;p.ckOrder=0}document.getElementById('overlay').classList.remove('show');document.getElementById('m-undo')?.remove();showToast('已撤销');render()}function showHistoryModal(){const bg=document.createElement('div');bg.style.cssText='position:fixed;inset:0;z-index:200;background:rgba(245,245,247,.97);display:flex;flex-direction:column';bg.id='m-hist';let h='<div style="flex:1;overflow:auto;padding:20px 16px"><div style="display:flex;justify-content:space-between;margin-bottom:20px"><div style="font-size:20px;font-weight:700;color:#1a1a1a">签到记录</div><button style="padding:6px 16px;border-radius:6px;font-size:13px;background:rgba(0,0,0,.04);border:1px solid rgba(0,0,0,.1);color:rgba(0,0,0,.4);cursor:pointer;font-family:inherit" onclick="document.getElementById(\'m-hist\').remove()">关闭</button></div>';if(!roundHistory.length)h+='<div style="color:rgba(0,0,0,.3);text-align:center;margin-top:60px">暂无</div>';else roundHistory.slice().reverse().forEach(r=>{const t=r.data.length,c=r.data.filter(p=>p.checkedIn).length,ms=r.data.filter(p=>!p.checkedIn);h+='<div style="background:#fff;border-radius:12px;border:1px solid rgba(0,0,0,.06);padding:16px;margin-bottom:12px"><div style="display:flex;justify-content:space-between;margin-bottom:10px"><div><b style="color:#1a1a1a">第 '+r.round+' 轮</b> <span style="font-size:12px;color:rgba(0,0,0,.35)">'+r.bus+'车 · '+r.time+(r.elapsed!=null?' · '+fmtTime(r.elapsed):'')+'</span></div><span style="font-size:12px;padding:2px 10px;border-radius:4px;background:'+(c===t?'rgba(0,230,118,.1)':'rgba(255,107,107,.1)')+';color:'+(c===t?'#00C853':'#FF6B6B')+'">'+c+'/'+t+'</span></div>';if(ms.length)h+='<div style="font-size:11px;color:#FF6B6B;margin-bottom:4px">未到：</div><div style="font-size:14px;color:rgba(0,0,0,.5);line-height:1.8">'+ms.map(p=>p.name).join('、')+'</div>';else h+='<div style="font-size:13px;color:rgba(0,200,83,.7)">全员到齐 ✓</div>';h+='</div>'});h+='</div>';bg.innerHTML=h;document.body.appendChild(bg)}function render(){const br=roster.filter(p=>p.bus===currentBus),ck=br.filter(p=>p.checkedIn).length,tot=br.length,unc=tot-ck,buses=[...new Set(roster.map(p=>p.bus||1))].sort(),q=searchQuery.trim().toLowerCase(),fl=q?br.filter(p=>p.name.toLowerCase().includes(q)):br,ny=fl.filter(p=>!p.checkedIn).sort((a,b)=>a.id-b.id),dn=fl.filter(p=>p.checkedIn).sort((a,b)=>b.ckOrder-a.ckOrder);let h='<div class="header"><div style="display:flex;justify-content:space-between;margin-bottom:10px"><div style="display:flex;align-items:center;gap:8px"><span style="font-size:15px;font-weight:600;color:rgba(0,0,0,.5)">NFC 签到</span><span style="font-size:12px;color:rgba(0,0,0,.3);background:rgba(0,0,0,.06);padding:2px 8px;border-radius:4px">第'+round+'轮</span></div><div style="display:flex;gap:6px"><button style="padding:5px 12px;border-radius:6px;font-size:12px;background:rgba(0,230,118,.12);border:1px solid rgba(0,230,118,.25);color:#00E676;cursor:pointer;font-family:inherit" onclick="demoScan()">模拟刷卡</button>';if(roundHistory.length)h+='<button style="padding:5px 10px;border-radius:6px;font-size:12px;background:rgba(0,0,0,.04);border:1px solid rgba(0,0,0,.1);color:rgba(0,0,0,.4);cursor:pointer;font-family:inherit" onclick="showHistoryModal()">记录</button>';h+='</div></div><div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;padding:8px 12px;border-radius:8px;background:'+(timerRunning?'rgba(0,0,0,.03)':'transparent')+';border:1px solid '+(timerRunning?'rgba(0,0,0,.06)':'transparent')+'"><button onclick="toggleTimer()" style="padding:5px 14px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;background:'+(timerRunning?'rgba(255,107,107,.12)':'rgba(0,176,255,.12)')+';border:1px solid '+(timerRunning?'rgba(255,107,107,.25)':'rgba(0,176,255,.25)')+';color:'+(timerRunning?'#FF6B6B':'#00B0FF')+'">'+(timerRunning?'⏸ 停止计时':'▶ 开始计时')+'</button>';if(timerRunning){const s=timerElapsed;h+='<span id="timer-display" style="font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;color:'+(s>300?'#FF6B6B':s>120?'#FFB000':'#00E676')+'">'+fmtTime(s)+'</span>'}else if(timerElapsed>0)h+='<span style="font-size:13px;color:rgba(0,0,0,.3)">上次 '+fmtTime(timerElapsed)+'</span>';h+='</div>';if(buses.length>1){h+='<div style="display:flex;gap:8px;margin-bottom:10px">';buses.forEach(b=>{const bc=roster.filter(p=>p.bus===b&&p.checkedIn).length,bt=roster.filter(p=>p.bus===b).length;h+='<button onclick="switchBus('+b+')" style="flex:1;padding:8px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;text-align:center;background:'+(b===currentBus?'rgba(0,176,255,.12)':'rgba(0,0,0,.04)')+';border:1px solid '+(b===currentBus?'rgba(0,176,255,.3)':'rgba(0,0,0,.08)')+';color:'+(b===currentBus?'#00B0FF':'rgba(0,0,0,.4)')+'">'+b+'车 <span style="font-size:11px;opacity:.6">'+bc+'/'+bt+'</span></button>'});h+='</div>'}h+='<div style="display:flex;align-items:center;gap:12px;background:rgba(0,0,0,.03);border-radius:12px;padding:12px 16px;border:1px solid rgba(0,0,0,.06)"><div style="flex:1"><div style="font-size:36px;font-weight:800;line-height:1;color:'+(unc>0?'#FF6B6B':'#00C853')+'">'+unc+'</div><div style="font-size:13px;color:rgba(0,0,0,.45);margin-top:4px">'+(unc>0?'未到':'全员到齐')+'</div></div><div style="flex:2"><div style="height:8px;border-radius:4px;background:rgba(0,0,0,.08);overflow:hidden"><div style="height:100%;border-radius:4px;background:'+(ck===tot?'linear-gradient(90deg,#00E676,#69F0AE)':'linear-gradient(90deg,#00B0FF,#00E5FF)')+';width:'+(tot?(ck/tot*100):0)+'%;transition:width .5s"></div></div><div style="display:flex;justify-content:space-between;font-size:12px;color:rgba(0,0,0,.35);margin-top:4px"><span>已到'+ck+'</span><span>共'+tot+'</span></div></div></div>';h+='<div style="display:flex;gap:8px;margin-top:10px"><button onclick="showNewRoundModal()" style="flex:1;padding:10px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;background:rgba(0,176,255,.08);border:1px solid rgba(0,176,255,.2);color:#00B0FF">新一轮</button><button onclick="showAddModal()" style="flex:1;padding:10px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;background:rgba(255,176,0,.08);border:1px solid rgba(255,176,0,.2);color:#FFB000">+ 加人</button><button onclick="showRemoveModal()" style="flex:1;padding:10px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;background:rgba(255,107,107,.08);border:1px solid rgba(255,107,107,.2);color:#FF6B6B">- 减人</button></div>';h+='<div style="position:relative;margin-top:10px"><input id="search-input" value="'+searchQuery.replace(/"/g,'&quot;')+'" oninput="setSearch(this.value)" placeholder="搜索姓名..." style="width:100%;padding:10px 14px 10px 36px;border-radius:8px;font-size:15px;background:rgba(0,0,0,.04);border:1px solid rgba(0,0,0,.1);color:#1a1a1a;outline:none"><span style="position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:14px;color:rgba(0,0,0,.3)">🔍</span>';if(searchQuery)h+='<button onclick="clearSearch()" style="position:absolute;right:10px;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.1);border:none;border-radius:50%;width:20px;height:20px;display:flex;align-items:center;justify-content:center;color:rgba(0,0,0,.4);font-size:12px;cursor:pointer">✕</button>';h+='</div></div><div style="padding:0 16px 100px">';if(ny.length){h+='<div style="font-size:12px;font-weight:600;color:#FF6B6B;padding:8px 0;border-bottom:1px solid rgba(255,107,107,.15);margin-bottom:4px;display:flex;align-items:center;gap:6px"><span style="width:6px;height:6px;border-radius:50%;background:#FF6B6B;display:inline-block"></span>未到·'+ny.length+'人';if(q)h+='<span style="color:rgba(0,0,0,.3);font-weight:400">（搜索）</span>';h+='</div>';ny.forEach(p=>{h+='<div class="'+(p.id===justId?'row-enter':'')+'" onclick="demoClick('+p.id+')" style="display:flex;align-items:center;padding:12px;border-bottom:1px solid rgba(0,0,0,.06);cursor:pointer"><div style="flex:1"><div style="font-size:18px;font-weight:600;color:#1a1a1a">'+p.name;if(p.isTemp)h+='<span style="font-size:11px;color:#FFB000;margin-left:6px">临时</span>';h+='</div></div>';if(p.phone)h+='<a href="tel:'+p.phone+'" onclick="event.stopPropagation()" style="width:36px;height:36px;border-radius:8px;background:rgba(0,230,118,.08);border:1px solid rgba(0,230,118,.15);display:flex;align-items:center;justify-content:center;text-decoration:none;margin-left:8px"><span style="font-size:16px">📞</span></a>';h+='</div>'})}if(dn.length){h+='<div style="font-size:12px;font-weight:600;color:rgba(0,180,83,.6);padding:12px 0 8px;border-bottom:1px solid rgba(0,180,83,.12);margin-top:8px;margin-bottom:4px;display:flex;align-items:center;gap:6px"><span style="width:6px;height:6px;border-radius:50%;background:rgba(0,180,83,.6);display:inline-block"></span>已到·'+dn.length+'人</div>';dn.forEach(p=>{h+='<div class="'+(p.id===justId?'row-enter':'')+'" style="display:flex;align-items:center;padding:10px 12px;border-bottom:1px solid rgba(0,0,0,.04);opacity:.6"><div style="width:28px;height:28px;border-radius:6px;background:rgba(0,180,83,.08);display:flex;align-items:center;justify-content:center;font-size:12px;color:rgba(0,180,83,.7);margin-right:12px">✓</div><div style="flex:1"><span style="font-size:15px;color:rgba(0,0,0,.5)">'+p.name;if(p.isTemp)h+='<span style="font-size:11px;color:rgba(255,176,0,.6);margin-left:6px">临时</span>';h+='</span></div><span style="font-size:11px;color:rgba(0,0,0,.3);margin-right:8px">'+p.time+'</span><button onclick="showUndoModal('+p.id+')" style="padding:4px 10px;border-radius:6px;font-size:11px;background:rgba(0,0,0,.04);border:1px solid rgba(0,0,0,.1);color:rgba(0,0,0,.4);cursor:pointer;font-family:inherit">撤销</button></div>'})}if(q&&!ny.length&&!dn.length)h+='<div style="text-align:center;padding:40px 0;color:rgba(0,0,0,.3)">未找到「'+q+'」</div>';h+='</div>';document.getElementById('app').innerHTML=h}render();connectWS()</script></body></html>"""

def main():
    import uvicorn
    print("="*50);print("  NFC 签到系统 v5.1");print("="*50);print()
    load_roster();reader=NFCReader();app=create_app(reader)
    print(f"\n[INFO] http://localhost:{CONFIG['port']}\n")
    uvicorn.run(app,host=CONFIG["host"],port=CONFIG["port"])

if __name__=="__main__":main()
