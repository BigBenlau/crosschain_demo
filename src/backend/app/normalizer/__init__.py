"""正規化模組對外入口。

本檔導出正規化服務單例，供索引流程調用。
"""

from app.normalizer.service import normalizer_service

__all__ = ["normalizer_service"]
