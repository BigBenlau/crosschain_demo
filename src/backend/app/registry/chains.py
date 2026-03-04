"""鏈註冊表定義。

本檔負責：
- 定義鏈配置資料結構 `ChainConfig`
- 從全域設定組裝 Ethereum + TARGET_CHAIN 的監控配置
- 透過目標鏈 RPC 的 `eth_chainId` 動態獲取 `chain_id`
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(frozen=True)
class ChainConfig:
    key: str
    chain_id: int
    rpc_url: str
    start_block: int
    finality_depth: int


def _hex_to_int(value: Any) -> int | None:
    """將 JSON-RPC 回傳值（hex/int）轉為 int。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return None


def _rpc_get_chain_id(rpc_url: str) -> int:
    """呼叫 `eth_chainId` 取得鏈 ID，失敗時拋出例外。"""
    if not rpc_url:
        raise RuntimeError("target_chain_rpc_url is empty")

    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"target chain eth_chainId request failed: {exc}") from exc

    if result.get("error"):
        raise RuntimeError(f"target chain eth_chainId rpc error: {result['error']}")

    chain_id = _hex_to_int(result.get("result"))
    if chain_id is None:
        raise RuntimeError("target chain eth_chainId rpc returned invalid result")
    return chain_id


def get_chain_registry(settings: Settings) -> list[ChainConfig]:
    """返回索引器使用的雙鏈配置（目標鏈 chain_id 由 RPC 自動解析）。"""
    target_chain_id = _rpc_get_chain_id(settings.target_chain_rpc_url)

    return [
        ChainConfig(
            key="ethereum",
            chain_id=1,
            rpc_url=settings.eth_rpc_url,
            start_block=settings.eth_start_block,
            finality_depth=settings.eth_finality_depth,
        ),
        ChainConfig(
            key=settings.target_chain.lower(),
            chain_id=target_chain_id,
            rpc_url=settings.target_chain_rpc_url,
            start_block=settings.target_chain_start_block,
            finality_depth=settings.target_chain_finality_depth,
        ),
    ]
