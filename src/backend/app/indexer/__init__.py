"""索引器模組對外入口。

本檔提供索引器單例，供應用啟停流程調用。
"""

from app.indexer.service import indexer_service

__all__ = ["indexer_service"]
