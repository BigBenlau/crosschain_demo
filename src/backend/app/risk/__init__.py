"""風險分析模組出口。

本檔導出風險分析服務單例，供索引流程調用。
"""

from app.risk.service import risk_service

__all__ = ["risk_service"]
