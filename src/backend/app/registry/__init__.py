"""註冊表模組聚合出口。

本檔統一導出鏈配置與協議配置的讀取函式。
"""

from app.registry.chains import ChainConfig, get_chain_registry
from app.registry.protocols import ProtocolConfig, get_protocol_registry

__all__ = ["ChainConfig", "ProtocolConfig", "get_chain_registry", "get_protocol_registry"]
