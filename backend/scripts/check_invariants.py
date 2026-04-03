#!/usr/bin/env python
"""
不变量检查脚本入口

使用方法:
    cd backend
    DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/check_invariants.py
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.testing.check_invariants import main

if __name__ == "__main__":
    main()