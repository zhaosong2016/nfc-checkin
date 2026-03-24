# NFC 智能签到系统

深圳游学团 100 人 NFC 签到解决方案

## 快速开始（3 步上手）

### 第 1 步：安装依赖

```bash
# 安装 Python 3.10+（如果还没有）
# Windows: https://www.python.org/downloads/
# Mac: brew install python3

# 安装依赖包
pip install fastapi uvicorn pyscard websockets
```

### 第 2 步：准备名单

编辑 `roster.csv` 文件，填入人员信息：

```csv
name,uid,phone
张三,,13800001001
李四,,13800001002
...
```

> uid 列先留空，后面用注册工具绑定卡片

### 第 3 步：启动系统

```bash
# 插入 ACR122U 读卡器，然后运行：
python server.py
```

打开浏览器访问 **http://localhost:8000**，按 F11 全屏，搞定！

---

## 卡片注册

在使用之前，需要将每张 NFC 卡绑定到具体人员：

```bash
python register_cards.py
```

按提示逐张刷卡，系统自动绑定 UID 到名单中的人员。

---

## 硬件清单

| 物料 | 型号 | 数量 | 参考价 | 去哪买 |
|------|------|------|--------|--------|
| USB NFC 读卡器 | ACR122U | 1-2 台 | ¥60-80/台 | 淘宝/华强北 |
| NFC 卡片 | NTAG213 白卡 | 110 张 | ¥0.8-1.5/张 | 淘宝/华强北 |
| 笔记本电脑 | Windows/Mac | 1 台 | 自备 | - |
| 便携显示屏（可选）| 15.6寸 USB-C | 1 台 | ¥300-500 | 淘宝/京东 |

**在深圳华强北可以当天买齐所有硬件。**

---

## 文件说明

```
├── server.py           # 主服务（NFC 读卡 + Web 服务 + WebSocket）
├── register_cards.py   # 卡片注册工具
├── roster.csv          # 人员名单（运行后自动生成示例）
├── checkin_log.csv     # 签到日志（自动生成）
└── README.md           # 本文件
```

---

## 现场部署 Checklist

- [ ] ACR122U 读卡器已测试
- [ ] 100 张 NFC 卡已全部绑定
- [ ] 10 张备用卡已准备
- [ ] 笔记本已充满电 + 带充电器
- [ ] 软件已测试通过
- [ ] 外接显示屏/投影（可选）已准备
- [ ] USB 延长线（可选）已准备

---

## 常见问题

**Q: 没有读卡器怎么测试？**
A: 直接运行 `python server.py`，前端页面会自动进入演示模式，可以点击名字模拟签到。

**Q: 读卡器识别不到？**
A: Windows 需要安装 ACR122U 驱动（通常即插即用）。Mac/Linux 免驱。

**Q: 一张卡可以给多个人用吗？**
A: 不可以，每张卡的 UID 全球唯一，只能绑定一个人。

**Q: 断网能用吗？**
A: 完全可以，所有数据本地处理，不需要网络。
