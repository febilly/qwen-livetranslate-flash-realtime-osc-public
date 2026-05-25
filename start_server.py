#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()


def _resource_path(*parts: str) -> Path:
    """Return an absolute path to a bundled resource (PyInstaller) or source file."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)

def check_api_key():
    """检查API Key是否配置"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("❌ 请设置DASHSCOPE_API_KEY环境变量")
        print("\n设置方法:")
        print("export DASHSCOPE_API_KEY=your_api_key_here")
        print("或者在.bashrc/.zshrc中添加上述命令")
        return False
    
    print(f"✅ API Key已配置: {api_key[:8]}...")
    return True

def main():
    """启动服务器"""
    print("🚀 实时语音翻译服务器启动检查")
    print("=" * 50)
    
    # 检查API Key
    if not check_api_key():
        sys.exit(1)
    
    # 检查静态文件
    static_path = _resource_path("static", "index.html")
    if not static_path.exists():
        print("❌ 静态文件不存在: static/index.html")
        sys.exit(1)
    
    print("✅ 所有检查通过")
    print("\n🌐 启动Web服务器...")
    print("访问地址: http://localhost:9023")
    print("按 Ctrl+C 停止服务器")
    print("-" * 50)
    
    # 启动服务器
    try:
        from web_server import run_server
        run_server()
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
