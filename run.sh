#!/bin/bash

# SuperChis 自动量产工具启动脚本

echo "SuperChis 自动量产工具"
echo "====================="
echo ""

# 检查Python版本
python_version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
echo "Python版本: $python_version"

# 检查依赖
echo "检查依赖..."
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "未找到PySide6，正在安装..."
    pip3 install -r requirements.txt
fi

# 启动程序
echo ""
echo "启动GUI程序..."
python3 chismaker.py
