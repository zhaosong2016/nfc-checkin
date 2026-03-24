#!/bin/bash
# ==========================================
#  NFC 签到系统 - 一键启动
#  双击此文件即可启动签到系统
# ==========================================

# 获取脚本所在目录（不管从哪里双击都能找到文件）
cd "$(dirname "$0")"

echo ""
echo "=================================="
echo "  NFC 签到系统正在启动..."
echo "=================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 Python3，请先安装 Python"
    echo "   下载地址：https://www.python.org/downloads/"
    echo ""
    echo "按回车键退出..."
    read
    exit 1
fi

# 检查 server.py 是否存在
if [ ! -f "server.py" ]; then
    echo "❌ 未找到 server.py 文件"
    echo "   请确认 server.py 和本脚本在同一个文件夹内"
    echo ""
    echo "按回车键退出..."
    read
    exit 1
fi

# 检查依赖是否安装
python3 -c "import fastapi" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚙️  首次运行，正在安装依赖..."
    pip3 install fastapi uvicorn websockets pyscard 2>/dev/null
    echo ""
fi

# 等 1 秒让服务启动，再打开浏览器
(sleep 2 && open "http://localhost:8000") &

echo "✅ 系统已启动"
echo "✅ 浏览器将自动打开签到大屏"
echo ""
echo "📌 如需其他设备访问，请使用以下地址："

# 显示局域网 IP
IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | head -1 | awk '{print $2}')
if [ -n "$IP" ]; then
    echo "   👉 http://${IP}:8000"
fi
echo ""
echo "按 Ctrl+C 可停止系统"
echo "-----------------------------------"
echo ""

# 启动服务（这行会一直运行直到 Ctrl+C）
python3 server.py
