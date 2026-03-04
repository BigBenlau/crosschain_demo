"""協議事件解碼模組對外入口。

本檔導出日誌解碼服務，供索引器在寫入 `raw_logs` 前解碼資料。
"""

from app.decoder.service import decode_log

__all__ = ["decode_log"]
