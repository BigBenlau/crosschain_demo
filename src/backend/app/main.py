"""FastAPI 應用入口。

本檔負責：
- 初始化 FastAPI 應用
- 透過 lifespan 管理資料庫與索引器生命週期
- 對外提供基礎健康檢查 API
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router as api_router
from app.config import settings
from app.db import init_db
from app.indexer import indexer_service
from app.maintenance import maintenance_service
from app.risk import risk_service


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    risk_service.start()
    indexer_service.start()
    maintenance_service.start()
    try:
        yield
    finally:
        maintenance_service.stop()
        indexer_service.stop()
        risk_service.stop()


app = FastAPI(title="Crosschain MVP API", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/api/health")
def health() -> dict:
    snapshot = indexer_service.snapshot()
    risk_snapshot = risk_service.snapshot()
    maintenance_snapshot = maintenance_service.snapshot()
    return {
        "api": "ok",
        "env": settings.app_env,
        "targetChain": settings.target_chain,
        "targetChainExplorerBaseUrl": settings.target_chain_explorer_base_url,
        "rpcConfigured": bool(settings.eth_rpc_url and settings.target_chain_rpc_url),
        "configuredStartBlock": {
            "ethereum": settings.eth_start_block,
            "targetChain": settings.target_chain_start_block,
        },
        "indexer": {
            "running": snapshot.running,
            "lastError": snapshot.last_error,
            "lastCycleSeq": snapshot.last_cycle_seq,
            "pollSeconds": snapshot.poll_seconds,
            "lastIndexedBlockByChain": snapshot.last_indexed_block_by_chain,
            "lastChangedIds": snapshot.last_changed_ids,
            "lastChangedCount": snapshot.last_changed_count,
            "lastRiskEnqueuedCount": snapshot.last_risk_enqueued_count,
        },
        "risk": {
            "running": risk_snapshot.running,
            "pendingCount": risk_snapshot.pending_count,
            "lastError": risk_snapshot.last_error,
            "lastEnqueuedIds": risk_snapshot.last_enqueued_ids,
            "lastCompletedIds": risk_snapshot.last_completed_ids,
        },
        "maintenance": {
            "running": maintenance_snapshot.running,
            "lastError": maintenance_snapshot.last_error,
            "lastRunAt": maintenance_snapshot.last_run_at,
            "lastDeletedRemovedLogs": maintenance_snapshot.last_deleted_removed_logs,
            "lastDeletedExecutedLogs": maintenance_snapshot.last_deleted_executed_logs,
            "lastArchivedFailedTxs": maintenance_snapshot.last_archived_failed_txs,
            "lastVacuumAt": maintenance_snapshot.last_vacuum_at,
            "lastVacuumReason": maintenance_snapshot.last_vacuum_reason,
            "archiveDir": maintenance_snapshot.archive_dir,
            "pollSeconds": maintenance_snapshot.poll_seconds,
            "enabled": settings.maintenance_enabled,
        },
    }
