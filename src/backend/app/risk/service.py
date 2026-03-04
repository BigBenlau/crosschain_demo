"""風險分析服務（規則 + 可選 AI 補充）。

本檔負責：
- 對交易主表做規則型風險判定
- 在配置可用時調用 AI 生成補充說明
- 將結果寫入 `risk_reports`
"""

import json
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RiskReport, XChainTx


class RiskService:
    """MVP 風險分析器。"""

    def analyze_transactions(self, db: Session, canonical_ids: list[str]) -> list[str]:
        """分析指定交易，回傳成功更新風險報告的 canonical id 列表。"""
        if not canonical_ids:
            return []

        stmt = select(XChainTx).where(XChainTx.canonical_id.in_(canonical_ids))
        txs = db.execute(stmt).scalars().all()

        updated_ids: list[str] = []
        for tx in txs:
            verdict, score, factors, summary = self._rule_assessment(tx)
            ai_summary = self._ai_assessment(tx, verdict, factors)
            final_summary = ai_summary if ai_summary else summary
            self._upsert_report(db, tx.canonical_id, verdict, score, factors, final_summary)
            updated_ids.append(tx.canonical_id)

        db.commit()
        return updated_ids

    def _rule_assessment(self, tx: XChainTx) -> tuple[str, int, list[str], str]:
        """基於狀態與失敗分類產生最小規則判定。"""
        factors: list[str] = []
        verdict = "SAFE"
        score = 15

        if tx.status == "FAILED":
            verdict = "HIGH_RISK"
            score = 90
            factors.append("目的鏈執行失敗")
        elif tx.status == "STUCK":
            verdict = "WARNING"
            score = 70
            if tx.failure_category == "STUCK_NO_VERIFY":
                factors.append("長時間未進入驗證階段")
            elif tx.failure_category == "STUCK_NEED_EXECUTION":
                factors.append("已驗證但長時間未執行")
            else:
                factors.append("交易流程超時停滯")
        elif tx.status == "VERIFIED":
            verdict = "WARNING"
            score = 45
            factors.append("交易仍在中間態，尚未完成執行")
        elif tx.status == "SENT":
            verdict = "WARNING"
            score = 35
            factors.append("交易剛發送，需等待後續驗證/執行")
        elif tx.status == "EXECUTED":
            verdict = "SAFE"
            score = 10
            factors.append("交易已完成執行")
        else:
            verdict = "UNKNOWN"
            score = 50
            factors.append("狀態未知，需人工核對")

        summary = f"規則判定結果：{verdict}，分數 {score}。"
        return verdict, score, factors, summary

    def _ai_assessment(self, tx: XChainTx, verdict: str, factors: list[str]) -> str | None:
        """在可用時調用 AI 產生補充敘述。"""
        if not settings.ai_api_key:
            return None

        payload = {
            "model": settings.ai_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "你是區塊鏈安全分析助手，請輸出簡短中文風險摘要（不超過80字）。",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "protocol": tx.protocol,
                            "status": tx.status,
                            "failure_category": tx.failure_category,
                            "rule_verdict": verdict,
                            "rule_factors": factors,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            settings.ai_base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.ai_api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=settings.ai_timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None

        choices = result.get("choices", [])
        if not choices:
            return None
        message = choices[0].get("message", {})
        content = message.get("content")
        return content.strip() if isinstance(content, str) and content.strip() else None

    def _upsert_report(
        self,
        db: Session,
        canonical_id: str,
        verdict: str,
        score: int,
        factors: list[str],
        summary: str,
    ) -> None:
        """更新或新增單筆風險報告。"""
        stmt = (
            select(RiskReport)
            .where(RiskReport.canonical_id == canonical_id)
            .order_by(desc(RiskReport.analyzed_at), desc(RiskReport.id))
        )
        latest = db.execute(stmt).scalars().first()

        if latest is None:
            latest = RiskReport(canonical_id=canonical_id, verdict=verdict, risk_score=score)
            db.add(latest)

        latest.verdict = verdict
        latest.risk_score = score
        latest.risk_factors_json = json.dumps(factors, ensure_ascii=False)
        latest.analysis_summary = summary
        latest.ai_model = settings.ai_model if settings.ai_api_key else None
        latest.prompt_version = "v1"


risk_service = RiskService()
