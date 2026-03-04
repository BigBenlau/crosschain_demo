"""API 路由模組出口。

本檔負責導出 API router，供主應用掛載。
"""

from app.api.routes import router

__all__ = ["router"]
