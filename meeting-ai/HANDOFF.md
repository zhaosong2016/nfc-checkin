# 游学会议系统 交接文档

最后更新：2026-04-30

## 系统概述

给游学活动用的实时会议系统。主持人创建行程和会议室，参会者扫码加入，实时发言，主持人触发 AI 总结。

## 访问地址

| 角色 | 地址 |
|------|------|
| 主持人 | https://myspaceone.com/meeting |
| 参会者 | 扫二维码（主持人页面生成，格式：https://myspaceone.com/m?trip=XXXX） |

## 核心功能

- **行程**：主持人创建，设置名称和管理员密码，获得二维码
- **会议室**：同一行程下可创建多个并行会议室（如主会场、分组A）
- **实时发言墙**：WebSocket 广播，所有人实时看到新发言
- **AI 总结**：主持人手动触发，Claude API 分析所有发言，输出"最具新颖性的5个观点 + 最好的5个问题"，推送到所有人屏幕
- **精华评选**：AI 从发言中选出最有价值的5条，参会者投票选第一
- **管理员模式**：行程页面右下角"管理员登录"，输密码后可创建会议室、触发总结/评选、修改 AI 提示词

## 技术架构

- **语言/框架**：Python 3 + FastAPI + WebSocket
- **前端**：单文件内嵌 HTML/JS，无框架，移动端优先
- **AI**：Claude API（claude-opus-4-7），通过 https://api.aicodewith.com 调用
- **数据持久化**：JSON 文件（`meeting-ai/data.json`），重启不丢数据
- **端口**：8001
- **服务器**：49.233.127.228，路径 `/root/nfc-checkin/meeting-ai/`

## 服务器操作

```bash
# 启动
nohup python3 /root/nfc-checkin/meeting-ai/server.py > /root/meeting.log 2>&1 &

# 查看日志
tail -f /root/meeting.log

# 重启
pkill -f 'meeting-ai/server.py'; sleep 1; nohup python3 /root/nfc-checkin/meeting-ai/server.py > /root/meeting.log 2>&1 &

# 部署最新代码
cd /root/nfc-checkin && git pull && pkill -f 'meeting-ai/server.py'; sleep 1; nohup python3 /root/nfc-checkin/meeting-ai/server.py > /root/meeting.log 2>&1 &
```

## Nginx 配置（已配置）

```nginx
location /meeting/ws/ { proxy_pass http://127.0.0.1:8001; ... }
location /meeting     { proxy_pass http://127.0.0.1:8001; ... }
location /m           { proxy_pass http://127.0.0.1:8001; ... }
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /meeting/api/trips | 创建行程 |
| GET  | /meeting/api/trips/{id} | 获取行程信息 |
| POST | /meeting/api/trips/{id}/rooms | 创建会议室（需密码） |
| GET  | /meeting/api/rooms/{id} | 获取会议室信息 |
| POST | /meeting/api/rooms/{id}/messages | 发言 |
| POST | /meeting/api/rooms/{id}/summarize | AI总结（需密码） |
| POST | /meeting/api/rooms/{id}/rank | AI评选精华（需密码） |
| POST | /meeting/api/rooms/{id}/vote | 投票 |
| GET/POST | /meeting/api/trips/{id}/prompt | 查看/修改AI提示词（需密码） |
| WS   | /meeting/ws/{room_id} | WebSocket 实时连接 |

## AI 提示词

默认提示词要求 AI 输出：
1. 最具新颖性的5个观点（注明发言者，说明为何新颖）
2. 最好的5个问题（注明发言者，说明为何好）

主持人可在管理员模式下修改提示词，修改后立即生效。

## 已知问题 / 待完成

- [ ] 测试未完成（2026-04-30 赵嵩回家继续测）
- [ ] 数据无过期清理机制（长期运行会积累旧行程数据）
- [ ] 服务器重启后需手动启动（未配置 systemd 自启）

## 文件结构

```
meeting-ai/
├── server.py       # 主程序（FastAPI + 内嵌HTML）
├── requirements.txt
├── data.json       # 运行时生成，存储所有行程/会议室/发言数据
└── HANDOFF.md      # 本文件
```
