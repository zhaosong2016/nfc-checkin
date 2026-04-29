# NFC 签到系统 - 交接文档

## 当前卡点（2026-04-29 深夜）

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
