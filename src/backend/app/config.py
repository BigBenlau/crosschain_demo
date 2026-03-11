"""集中管理後端配置。

本檔負責：
- 從環境變數與 `.env` 讀取系統配置
- 提供鏈、RPC、Indexer、AI、資料庫等參數
- 統一輸出全域 `settings` 供其他模組使用
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


"""`Settings` 參數來源說明。

讀取順序：
1. 系統環境變數
2. `src/backend/.env`（由 `env_file` 的絕對路徑指定）

說明：
- 本類別不提供預設值，所有參數需由環境變數或 `.env` 注入。
"""

BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    # 基礎應用配置
    app_name: str
    app_env: str
    app_port: int

    # 目標鏈配置（第二條鏈）
    target_chain: str

    # RPC 節點配置（全鏈上數據來源，全部由 .env 管理）
    api_key: str
    eth_rpc_url: str
    target_chain_rpc_url: str

    # 歷史回填起始區塊配置
    eth_start_block: int
    target_chain_start_block: int

    # 最終性深度配置（safe head = latest - finality_depth）
    eth_finality_depth: int
    target_chain_finality_depth: int

    # Indexer 輪詢與掃描粒度配置
    indexer_poll_seconds: int
    indexer_chunk_size: int
    stuck_timeout_minutes: int

    # 協議事件 topic0 配置（逗號分隔）
    layerzero_topic0s: str
    wormhole_topic0s: str
    layerzero_sent_topics: str
    layerzero_verified_topics: str
    layerzero_executed_topics: str
    layerzero_failed_topics: str
    wormhole_sent_topics: str
    wormhole_executed_topics: str

    # 協議合約地址配置（逗號分隔，僅鏈上 RPC 過濾使用）
    layerzero_ethereum_endpoints: str
    layerzero_target_endpoints: str
    wormhole_ethereum_core_contracts: str
    wormhole_target_core_contracts: str
    wormhole_ethereum_token_bridges: str
    wormhole_target_token_bridges: str

    # 協議方向判斷配置（僅用於 EID/Wormhole Chain ID 方向校驗）
    layerzero_ethereum_eid: int
    layerzero_target_eid: int
    wormhole_ethereum_chain_id: int
    wormhole_target_chain_id: int

    # AI 風險分析配置
    ai_api_key: str
    ai_base_url: str
    ai_model: str
    ai_timeout_seconds: int
    ai_batch_size: int
    ai_batch_max_size: int
    ai_max_prompt_chars: int
    ai_max_output_tokens: int
    ai_temperature: float

    # 資料庫連線配置
    db_path: str

    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=False,
    )

    def parse_csv(self, raw_value: str) -> list[str]:
        """將逗號分隔字串轉為去空白列表。"""
        return [item.strip() for item in raw_value.split(",") if item.strip()]


settings = Settings()
