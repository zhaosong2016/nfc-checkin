# NFC 签到系统 - 交接文档

## 🔥 2026-05-27 待办（赵嵩去公司路上,带着 Pi）

### 优化已完成,但代码还没同步到 Pi

今天家里改了 server.py,**已 commit + push 到 GitHub**(commit `d2d7923`),但 Pi 上还是老版本。需要带 Pi 到公司后,**同步代码 + 重启 service**。

**这次改了什么:**
- 手工签到不再闪 1 秒绿屏(排队签到时严重卡顿,改后 ~0.3 秒/人)
- 顺手修了真 bug:之前手工签到只在前端本地改状态,**不发 ws 给 Pi**,等于伪签到 → 现在改成发 `manual_checkin` 给 Pi,Pi 处理 + 广播 + 推云端
- 刷卡那条路完全不变,继续闪绿屏
- 改动量:4 处微改,共 2 行加 2 行减

### 公司接手 Claude 要做的事(按顺序)

**前提:Pi 跟着赵嵩到公司了,公司电脑必须连上 Pi 当前的 WiFi 热点(Long1 或赵嵩当前用的)。**

```bash
# 1. 找 Pi(它在 iPhone 热点子网里,IP 通常 172.20.10.2)
arp -a | grep -E "88:a2:9e"     # Pi 的 MAC 前缀
# 看到 "88:a2:9e:3f:be:fc" 那行的 IP,就是备用 Pi

# 2. SSH 进去看状态(用户名 checkin2026,不是 checkin2026i!备用 Pi 没那个 i)
sshpass -p 'nfc2026' ssh checkin2026@172.20.10.2 "
  systemctl is-active nfc-checkin
  curl -s http://localhost:8000/api/roster | python3 -c 'import sys,json; d=json.load(sys.stdin); print(\"人数:\",len(d),\"已绑:\",sum(1 for p in d if p.get(\"uid\")))'
"

# 3. 同步最新 server.py(从 Mac 本地,iCloud 同步过来的最新版)
cd "/Users/songsongsong/Library/Mobile Documents/com~apple~CloudDocs/同步盘/INNOVATION MAP/赵嵩项目/202601编程思维课/nfc-checkin"
sshpass -p 'nfc2026' scp server.py checkin2026@172.20.10.2:~/nfc-checkin/server.py

# 4. 重启 service
sshpass -p 'nfc2026' ssh checkin2026@172.20.10.2 "echo nfc2026 | sudo -S systemctl restart nfc-checkin"

# 5. 验证
sshpass -p 'nfc2026' ssh checkin2026@172.20.10.2 "echo nfc2026 | sudo -S journalctl -u nfc-checkin -n 8 --no-pager"
# 应该看到 "[INFO] 已加载 31 人名单 / 主事件循环已就绪 / 已连接读卡器 / NFC轮询已启动"
```

### 验证手工签到优化是否生效

赵嵩手机连同一个热点,打开 `http://172.20.10.2:8000`:
1. 点一个**未签到**的人 → 应该弹"**确认点名**"对话框
2. 点"**确认签到**" → 对话框立刻消失 + 顶部 toast"X 已签到"闪一下 → **不再有 1 秒全屏绿色闪现**
3. 名字立刻从"未到"挪到"已到"

如果还闪绿屏,说明代码没真同步上去,看 `~/nfc-checkin/server.py` 里搜 `function doCheckin`,应该能看到 `silent` 这个参数。

### 已知问题:Pi 连热点不稳定(5/27 在家又复现)

赵嵩在家时 Pi 连 iPhone 热点反复掉线/连不上,试过的有效操作:
1. 关掉 iPhone 个人热点 → 重新打开 → **保持设置页打开别退出**
2. iPhone "最大兼容性" 默认开着(2.4GHz),不用反复确认
3. 拔 Pi 电源等 5 秒重新插
4. **Pi 凑近 iPhone 30cm 内**(信号问题不能完全排除)

如果到公司后 Pi 又连不上公司能用的热点,优先排查:Pi 上的 WiFi profile 里有没有那个 SSID,有没有保存对应密码。备用 Pi 当前的 WiFi profile 是 Long1 那台 iPhone 的,**换其他热点要先 SSH 进 Pi 用 nmcli 加新 WiFi**——但**前提是要先 SSH 上**,这是个鸡生蛋问题。

如果完全 SSH 不上,见下面"备用 Pi 信息"里的物理介入方案。

---

## 备用 Pi 信息(5/9 美国部署版,5/10 升级到 31 人名单)

**主入口**:`http://172.20.10.2:8000`(在 iPhone 热点子网下)
**SSH**:`sshpass -p 'nfc2026' ssh checkin2026@172.20.10.2`
- 用户名 `checkin2026`(注意!主 Pi 是 `checkin2026i`,这个备用 Pi 没那个 `i`)
- 密码 `nfc2026`
- sudo 密码也是 `nfc2026`
- hostname `checkin`(主 Pi 是 `checkin2`)
- MAC `88:a2:9e:3f:be:fc`

**已绑卡**:31 / 31 全部绑定(2026-05-10 在 Long1 热点下重新绑过一次,UID 跟主 Pi 不同,在备用 Pi 上 csv 里),美国带团时 100% 通过。

**这台 Pi 不是 git 仓库**(代码是 5/10 用 scp 推的),所以**改代码后只能 scp,不能 git pull**。

**Trixie 系统的几个坑**:
- pip 装包必须 `--break-system-packages --ignore-installed`(PEP 668)
- pyscard 的 import 名是 `smartcard` 不是 `pyscard`
- systemd User=root 时 Python 包必须 `sudo pip3 install` 全局装
- 新烧的卡 SSH 主机指纹会变,Mac 端 `ssh-keygen -R 172.20.10.2` 清旧记录
- iPhone 个人热点子网固定是 `172.20.10.0/28`,不管 SSID 叫啥

**待修问题(5/9 测试发现,至今没修)**:
1. **云端推送失效**——Pi 端签到 31 人,云端 `myspaceone.com/siliconvalley` 看到全部 `checkedIn=false`。怀疑 Airdoc/iPhone 热点出 https 失败。Pi 重启后从云端同步会拉到空状态。**带团时只看 Pi 直连页面,不依赖云端**。
2. **register_cards.py 改 csv 后 service 不会自动重读**——绑卡完必须 `sudo systemctl restart nfc-checkin` 才能让新 UID 生效
3. **IP 是 DHCP**——热点重启后可能换 IP,扫一下网段重定位

---

## 主 Pi 信息(2026-05-09 重烧版,5/27 已挂)

**主入口**:`http://192.168.1.107:8000`(以前在 CMCC-501 网络下),美国 Airdoc 时 IP 变成 172.20.10.2
**SSH**:`sshpass -p 'nfc2026' ssh checkin2026i@<IP>` —— **注意用户名是 `checkin2026i` 带 `i`**
- hostname `checkin2`
- MAC `88:a2:9e:55:de:69`
- 5/27 在家已挂(连不上热点,可能要重烧),目前以**备用 Pi 为主**

## 当前状态（2026-05-09，美国）

### 系统已可正常使用（Airdoc 热点）

**主入口**：`http://172.20.10.2:8000`（Pi 直连，**Airdoc 热点**下）
**备用显示**：`https://myspaceone.com/siliconvalley`（只看，绝对不要点撤销/新一轮）
**Pi 信息**：hostname `checkin2`，SSH `sshpass -p 'nfc2026' ssh checkin2026i@172.20.10.2`

**这次重做了什么**：
- SD 卡用 Pi Imager 全新烧录（系统是 **Debian 13 Trixie**，64-bit aarch64，不是之前的 Bookworm）
- WiFi 预配 Airdoc/01234567，开机自动连
- 重装 pcscd + 全套 Python 依赖（root 全局装，加 `--break-system-packages --ignore-installed`，因为 Trixie PEP668）
- systemd service `nfc-checkin` 已 enable，**重启自动起**
- IP 是 DHCP 拿的（172.20.10.2），不是预想的 172.20.10.50 静态 IP——但够用，先不折腾

**已绑卡（5/9 测试通过）**：**31 人全部绑定完毕**，全员刷卡端到端测试通过（2 分 06 秒完成 31 人签到）。
- 全部 UID 见 `roster.csv`，已 commit 到 git
- 旧卡 UID（赵嵩 `5A:74:0E:87:03:41:89`、周鹏 `15:32:48:BF`）已废弃，新卡 UID 前缀都是 `53:XX:C2/C1:26:94:00:01`

### ⚠️ 待修问题（5/9 发现，未解决）

**1. 云端推送失效（高优）**
- 现象：Pi 端 31 人都签到成功，但 `https://myspaceone.com/siliconvalley/api/roster` 上看到的全部 `checkedIn=false`、`uid` 也全空
- 影响：Pi 重启后从云端同步会拉到空状态，等于丢所有签到。带团时不在车上的人也看不到云端进度
- 怀疑：Airdoc 美国网络下 Pi 推 https://myspaceone.com 失败（GFW？SSL？超时？）
- 排查命令（要 Mac 在 Airdoc 网才能跑）：
  ```bash
  sshpass -p 'nfc2026' ssh checkin2026i@172.20.10.2 \
    "sudo journalctl -u nfc-checkin --since '30 minutes ago' | grep -iE 'cloud|push|http|error|warn'"
  ```
- 临时措施：带团时只看 Pi 直连页面，不依赖云端

**2. `register_cards.py` 改 csv 后 service 不会自动重读（中优）**
- 现象：用 `register_cards.py` 绑卡完，刷新卡仍报"未知卡片"
- 原因：service 启动时读 csv 进内存，运行中只看内存不看文件
- 解法：每次绑完必须 `sudo systemctl restart nfc-checkin`
- 改进方向：未来可以给 server.py 加个 `/api/reload_roster` 接口或 SIGHUP 信号

**3. IP 是 DHCP（低优）**
- 现状 172.20.10.2 是 DHCP 拿的，热点重启后可能变成别的 IP
- 想稳定的话还是要再配一次 nmcli 静态 IP（参考之前的 172.20.10.50 方案，要 SSH 进 Pi 配）
- 不急，目前能用

### 关键操作命令

```bash
# SSH 进 Pi
sshpass -p 'nfc2026' ssh checkin2026i@172.20.10.2

# 看服务状态
sudo systemctl status nfc-checkin
sudo journalctl -u nfc-checkin -f   # 实时日志

# 重启服务
sudo systemctl restart nfc-checkin

# 绑新卡（必须先停服务，读卡器只能被一个进程占）
sudo systemctl stop nfc-checkin
cd ~/nfc-checkin && sudo python3 register_cards.py
sudo systemctl start nfc-checkin
```

### 这次踩的新坑（Trixie 特有）

1. **pip 装包默认拒绝**（PEP 668）：必须加 `--break-system-packages`
2. **typing_extensions 冲突**：Debian 自带的没有 RECORD 文件，pip 拆不了。加 `--ignore-installed` 跳过
3. **systemd User=root 用不到 ~/.local/lib**：Python 包必须用 `sudo pip3 install`，root 全局装
4. **新烧的卡 SSH 主机指纹变了**：Mac 端要 `ssh-keygen -R <IP>` 清旧记录或者 `-o UserKnownHostsFile=/dev/null`
5. **iPhone 个人热点默认 5GHz**：必须开"最大兼容性"切到 2.4GHz，不然某些 Pi 看不到

---

### 明天要做的事：配置备用 Pi

明天在公司网络下，把备用 Pi 配置成和现在这台一样。步骤：

**第一步：找到备用 Pi 的 IP**
```bash
# 备用 Pi 接上网线或连公司 WiFi 后，在路由器管理页面找 IP
# 或者：
ping raspberrypi.local   # 默认 hostname
```

**第二步：SSH 进去部署**
```bash
ssh pi@<备用Pi的IP>   # 默认密码 raspberry，或 checkin2026i / nfc2026

# 安装依赖
sudo apt-get update && sudo apt-get install -y python3-pip pcscd
pip3 install fastapi uvicorn websockets pyscard httpx

# 克隆代码
git clone https://github.com/zhaosong2016/nfc-checkin.git
cd nfc-checkin
```

**第三步：创建 systemd service**
```bash
sudo tee /etc/systemd/system/nfc-checkin.service > /dev/null << 'EOF'
[Unit]
Description=NFC Check-in System
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/checkin2026i/nfc-checkin/server.py
WorkingDirectory=/home/checkin2026i/nfc-checkin
Restart=always
User=root
Environment=CLOUD_URL=https://myspaceone.com/siliconvalley
Environment=CLOUD_SECRET=nfc2026
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nfc-checkin
sudo systemctl start nfc-checkin
```

**第四步：验证**
```bash
systemctl status nfc-checkin
curl http://localhost:8000/api/roster
```

---

### 换热点/换网络

到美国换新热点时，SSH 进 Pi 跑：
```bash
sudo nmcli dev wifi connect '新热点名' password '新密码'
```
Pi 会断开当前 WiFi 连上新的，SSH 断开后等 10 秒用新 IP 重连。

---

### 给下一个 AI 的话

你好。这个系统的核心架构是：**Pi 是唯一数据源，云端是只读镜像**。

几个容易踩的坑：
1. **"没反应"的根本原因**：Pi 和云端状态不同步。Pi 认为某人已签到就不会推送，云端数字就不变。解法是在 Pi 直连页面点"新一轮"重置，不要在云端页面操作。
2. **云端操作不会到 Pi**：云端 WebSocket 消息只在云端处理，不会转发给 Pi。所有写操作（签到、撤销、新一轮）必须通过 Pi 直连页面。
3. **Pi 重启后会从云端同步状态**：startup 里有同步逻辑，正常。
4. **读卡器被占用**：跑 `register_cards.py` 前必须先 `sudo systemctl stop nfc-checkin`，用完再 start。
5. **新卡不识别**：ACR122U 只支持 13.56MHz 的卡（Mifare/NTAG），125kHz 的卡完全不兼容，绿灯不亮。

祝顺利。

---

## 旧卡点（2026-04-29 深夜）

### 问题描述

树莓派上，`nohup python3 server.py` 后台启动时，读卡器报错：
```
读卡器初始化失败: Failed to establish context: Access denied. (0x8010006A)
```

但同一个用户直接跑 `python3 -c "from smartcard.System import readers; print(readers())"` 完全正常，能读到卡。

**根本原因**：nohup 后台进程没有 D-Bus session，pcscd 的权限验证在这种环境下失效。已尝试 polkit 规则、socket 权限 777、--disable-polkit、移动初始化时序等多种方法均无效。

**明天的解法**：改用 systemd service 跑 server.py，systemd 能正确设置运行环境，是 Pi 上跑后台服务的标准方式。

### 明天要做的事

1. Pi 和 Mac 都连到公司 WiFi（同一个网络）
2. SSH 进 Pi：`ssh checkin2026i@checkin2.local`（密码 `nfc2026`）
3. 创建 systemd service 文件，让 server.py 开机自启
4. 测试完整链路：刷卡 → 云端实时显示

---

### 网络切换说明

**Pi 切换 WiFi 的方法**（不需要动 SD 卡，SSH 进去改）：

```bash
# SSH 进 Pi（需要先在同一个网络）
ssh checkin2026i@checkin2.local

# 扫描可用 WiFi
sudo nmcli dev wifi list

# 连接新 WiFi（替换 WiFi名 和 密码）
sudo nmcli dev wifi connect 'WiFi名' password '密码'
```

Pi 会自动断开当前 WiFi 并连上新的，SSH 会断开，等 10 秒后用新 IP 重连。

**注意**：Mac 和 Pi 必须在同一个 WiFi 下才能 SSH。切换时两个都要切。

---

## 项目目标

深圳硅谷游学团 NFC 签到系统。树莓派接 ACR122U 读卡器在车上运行，刷卡后实时推送到腾讯云服务器，所有人手机访问云端页面查看签到状态。

## 当前状态（2026-04-29 晚上）

### 已完成

- 代码仓库：https://github.com/zhaosong2016/nfc-checkin
- 云端服务器：腾讯云 `49.233.127.228`，SSH `root@49.233.127.228`（免密）
- 访问地址：`https://myspaceone.com/siliconvalley`（HTTPS，已配 nginx 反向代理）
- 名单：31人，已写入 `roster.csv`
- 赵嵩的卡已绑定，UID：`5A:74:0E:87:03:41:89`
- 树莓派→云端推送链路已实现（`CLOUD_URL` 环境变量控制）
- 新一轮重置会同步推送到云端
- **手动点名确认弹窗已完成**（点名字会弹出"确认点名"对话框，防误触）
- **浅色主题部分完成**：CSS 层（body、header、toast、modal、overlay）已改为白底

### 未完成（UI 改动，接着做）

**浅色主题 render() 函数内联样式还未改**，剩余约 15 处 `rgba(255,255,255,...)` 需替换为深色等价值：

| 位置 | 旧值 | 新值 |
|------|------|------|
| 统计卡片 bg/border | `rgba(255,255,255,.03/.05)` | `rgba(0,0,0,.03/.06)` |
| 进度条背景 | `rgba(255,255,255,.06)` | `rgba(0,0,0,.08)` |
| 统计文字（未到/共N） | `rgba(255,255,255,.35/.25)` | `rgba(0,0,0,.45/.35)` |
| 搜索框 bg/border/color | `rgba(255,255,255,.04/.08)` + `#fff` | `rgba(0,0,0,.04/.1)` + `#1a1a1a` |
| 搜索图标 | `rgba(255,255,255,.2)` | `rgba(0,0,0,.3)` |
| 搜索清除按钮 | `rgba(255,255,255,.1/.4)` | `rgba(0,0,0,.1/.4)` |
| 搜索标签文字 | `rgba(255,255,255,.2)` | `rgba(0,0,0,.3)` |
| 未到行分隔线 | `rgba(255,255,255,.04)` | `rgba(0,0,0,.06)` |
| 已到行分隔线+opacity | `rgba(255,255,255,.02)` + `.4` | `rgba(0,0,0,.04)` + `.6` |
| 已到姓名颜色 | `rgba(255,255,255,.6)` | `rgba(0,0,0,.5)` |
| 已到时间颜色 | `rgba(255,255,255,.15)` | `rgba(0,0,0,.3)` |
| 撤销按钮 | `rgba(255,255,255,.04/.08/.3)` | `rgba(0,0,0,.04/.1/.4)` |
| 无结果文字 | `rgba(255,255,255,.15)` | `rgba(0,0,0,.3)` |
| overlay 副标题 | `rgba(255,255,255,.4/.2)` | `rgba(0,0,0,.5/.4)` |
| showAddModal "添加到" | `rgba(255,255,255,.3)` | `rgba(0,0,0,.4)` |
| showUndoModal 姓名 | `color:#fff` | `color:#1a1a1a` |
| showHistoryModal | 整体深色背景 | 改为浅色（参考 overlay 风格） |

**做完后需要**：
```bash
HTTPS_PROXY=http://127.0.0.1:7897 git push origin main
ssh root@49.233.127.228 "cd /root/nfc-checkin && git pull && pkill -f 'python3 server.py'; nohup python3 server.py > /root/nfc.log 2>&1 &"
```

### 其他未完成

- 树莓派尚未配置 WiFi（SD 卡读卡器不在手边，晚上处理）
- 树莓派上尚未部署代码
- 完整链路（树莓派刷卡→云端实时显示）尚未端到端测试

### 已知问题

- Mac 上 pyscard 多线程不稳定，server.py 的 NFC 轮询线程在 macOS 读不到卡。**树莓派（Linux）上无此问题**，不需要修代码。

---

## 架构说明

```
树莓派（读卡器 + server.py）
  ↓ 刷卡时 POST /api/push
腾讯云 server.py（接收推送，广播 WebSocket）
  ↓
所有人手机浏览器（myspaceone.com/siliconvalley）
```

树莓派启动命令：
```bash
CLOUD_URL=https://myspaceone.com/siliconvalley python3 server.py
```

不设 `CLOUD_URL` 就是纯本地模式（不推送云端）。

推送密钥：`nfc2026`（`CLOUD_SECRET` 环境变量，默认值已写在代码里）

---

## 晚上需要做的事

### 第一步：配置树莓派 WiFi

把树莓派 SD 卡插到 Mac，在 `boot` 分区根目录新建文件 `wpa_supplicant.conf`，内容如下（替换 WiFi 名和密码）：

```
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=CN

network={
    ssid="你的WiFi名"
    psk="你的WiFi密码"
    key_mgmt=WPA-PSK
}
```

同时在 `boot` 分区根目录新建一个空文件 `ssh`（无后缀），开启 SSH。

SD 卡插回树莓派，开机，等 30 秒。

### 第二步：找到树莓派 IP

在 Mac 终端运行：
```bash
ping raspberrypi.local
```
或者在路由器管理页面查看。

### 第三步：SSH 进树莓派部署代码

```bash
ssh pi@raspberrypi.local
# 默认密码 raspberry，如果改过就用改过的

# 进去后：
sudo apt-get update && sudo apt-get install -y python3-pip
pip3 install fastapi uvicorn websockets pyscard
git clone https://github.com/zhaosong2016/nfc-checkin.git
cd nfc-checkin
CLOUD_URL=https://myspaceone.com/siliconvalley python3 server.py
```

### 第四步：测试完整链路

1. 手机打开 `https://myspaceone.com/siliconvalley`
2. 把赵嵩的卡（UID: `5A:74:0E:87:03:41:89`）放到读卡器上
3. 手机上应实时看到赵嵩被签到

---

## 其他卡片注册

其他人的卡还没绑定。卡到手后，在树莓派上运行：
```bash
cd nfc-checkin
python3 register_cards.py
```
按提示逐张刷卡绑定，自动保存到 `roster.csv`，然后：
```bash
git add roster.csv && git commit -m "绑定NFC卡" && git push
```

---

## 云端服务器管理

```bash
ssh root@49.233.127.228

# 查看日志
tail -f /root/nfc.log

# 重启服务
pkill -f "python3 server.py"
cd /root/nfc-checkin && git pull
nohup python3 server.py > /root/nfc.log 2>&1 &

# nginx 配置
cat /etc/nginx/conf.d/myspaceone.conf
nginx -s reload
```

---

## 文件结构

```
nfc-checkin/
├── server.py          # 主服务（NFC读卡 + WebSocket + 云端推送接收）
├── register_cards.py  # 卡片注册工具
├── roster.csv         # 31人名单（赵嵩已绑卡）
├── checkin_log.csv    # 签到日志（运行时自动生成）
└── HANDOFF.md         # 本文件
```
