# NFC 签到系统 - 交接文档

## 项目目标

深圳硅谷游学团 NFC 签到系统。树莓派接 ACR122U 读卡器在车上运行，刷卡后实时推送到腾讯云服务器，所有人手机访问云端页面查看签到状态。

## 当前状态（2026-04-29 下午）

### 已完成

- 代码仓库：https://github.com/zhaosong2016/nfc-checkin
- 云端服务器：腾讯云 `49.233.127.228`，SSH `root@49.233.127.228`（免密）
- 访问地址：`https://myspaceone.com/siliconvalley`（HTTPS，已配 nginx 反向代理）
- 名单：31人，已写入 `roster.csv`
- 赵嵩的卡已绑定，UID：`5A:74:0E:87:03:41:89`
- 树莓派→云端推送链路已实现（`CLOUD_URL` 环境变量控制）
- 新一轮重置会同步推送到云端

### 未完成

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
