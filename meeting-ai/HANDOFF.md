# 游学会议系统 交接文档

最后更新：2026-05-26

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
| POST | /meeting/api/trips/{id}/topics/enable | 开关议题模块（需密码） |
| POST | /meeting/api/trips/{id}/topics/new_round | 开启新一轮议题（需密码） |
| POST | /meeting/api/trips/{id}/topics/propose_open | 控制是否允许参会者提议题（需密码） |
| POST | /meeting/api/trips/{id}/topics | 提议题 |
| POST | /meeting/api/trips/{id}/topics/{tid}/signup | 报名议题 |
| POST | /meeting/api/trips/{id}/topics/{tid}/unsignup | 取消报名 |
| POST | /meeting/api/trips/{id}/topics/{tid}/toggle_full | 标记满员（需密码） |
| POST | /meeting/api/trips/{id}/topics/{tid}/cancel | 取消议题（需密码） |
| POST | /meeting/api/trips/{id}/topics/{tid}/delete | 删除议题（需密码） |
| POST | /meeting/api/trips/{id}/mentors/enable | 开关导师评分模块（需密码） |
| POST | /meeting/api/trips/{id}/mentors | 添加导师（需密码） |
| POST | /meeting/api/trips/{id}/mentors/reorder | 调整导师顺序（需密码） |
| POST | /meeting/api/trips/{id}/mentors/{mid}/avatar | 上传头像（需密码，multipart） |
| POST | /meeting/api/trips/{id}/mentors/{mid}/rate | 提交评分 |
| GET  | /meeting/api/trips/{id}/mentors/{mid}/my_rating?rater=X | 查看自己的评分 |
| GET  | /meeting/api/trips/{id}/mentors/admin?admin_password=X | 管理员查看全部评分 |
| GET  | /meeting/api/trips/{id}/mentors/export?admin_password=X | 导出评分数据 |

## AI 提示词

默认提示词要求 AI 输出：
1. 最具新颖性的5个观点（注明发言者，说明为何新颖）
2. 最好的5个问题（注明发言者，说明为何好）

主持人可在管理员模式下修改提示词，修改后立即生效。

## 更新日志

### 2026-05-26

- **议题协作模块**：行程下新增"议题"完整流程
  - 管理员可开关议题模块、控制是否允许参会者提议题、开启新一轮
  - 参会者可提议题（题目、说明、所在公司、行业）、报名加入感兴趣的议题、取消报名
  - 管理员可标记议题满员、取消议题、删除议题
- **导师评分模块**：行程下新增"导师"完整流程
  - 管理员可开关模块、添加/删除导师、上传导师头像、调整顺序
  - 参会者可对每位导师按 6 个维度打分（内容、深度、案例、表达、互动、综合）+ 留言
  - 评分支持回看自己已打的分；管理员可看全部评分汇总，可导出
- **依赖新增**：`python-multipart`（FastAPI 文件上传必需，用于导师头像）
- **前端**：行程页新增 `tripTab` 状态切换（日程 / 议题 / 导师 三个 tab）

### 2026-05-07

- **日程模块**：行程页新增"日程 / 议题"两个 tab，管理员可逐天添加日程（日期 + 富文本），参会者横向滑动查看，默认定位到当天，顶部圆点导航
- **会议 → 议题**：tab 名称、列表标题、创建按钮全部改为"议题"
- **系统改名**：途说 → 前哨游学 OS
- **提示词移至议题级别**：每个新议题自动使用默认提示词，互不影响；管理员在议题内可修改，支持"恢复默认"；默认提示词升级为三段（发言总结 + 最新颖观点 + 最值得深入的问题）
- **参会者隐藏管理员入口**：扫码进入的用户不再显示"管理员登录"按钮
- **参会者无退出按钮**：扫码用户行程页不显示返回箭头，避免误入创建行程页
- **发言换行**：Enter 键改为换行，发送只能点按钮；发言墙支持 `white-space:pre-wrap` 正确显示换行

### 2026-05-01

- **参会者视图修复**：扫码进入 `/m?trip=XXXX` 时强制清除管理员状态，参会者不会看到管理员界面
- **二维码仅管理员可见**：行程页面的二维码/邀请卡片只对管理员显示
- **复制参会链接**：管理员行程页面新增"复制参会链接"按钮，一键复制正确的 `/m?trip=XXXX` 地址
- **管理员行程列表**：`/meeting` 首页自动读取 localStorage，显示"我的行程"列表，管理员可直接点击重新进入，无需保存二维码
- **精华发言名字修复**：服务端在返回 top5 时直接附带发言人姓名和内容，不再依赖客户端查找（之前 AI 返回 ID 不匹配时会显示乱码）
- **AI 评选不限数量**：提示词改为按质量选出所有值得关注的发言（通常 3-8 条），不再固定 5 条
- **投票排名逻辑重做**：投票前不显示排名，投票后按票数自动排序并显示名次和蓝色票数
- **每人 3 票**：可投给不同条目，可取消；右上角实时显示剩余票数

## 已知问题 / 待完成

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
