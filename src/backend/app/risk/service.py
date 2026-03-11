"""風險分析服務（規則 + 異步批量 AI 評審）。

本檔負責：
- 對交易主表做規則型風險判定
- 維護待檢查交易池與背景 worker
- 按批次調用 AI 分析多筆交易
- 將結果寫入 `risk_reports`
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.logging_utils import build_backend_file_logger
from app.models import RiskReport, XChainTimelineEvent, XChainTx


logger = build_backend_file_logger("xchain.risk", "indexer.log")
AI_RETRY_BACKOFF_SECONDS = 15

PROMPT_VERSION = "v5-zhipu-async-batch-risk-review"
SYSTEM_PROMPT = """你是跨鏈交易安全分析助手。你的任務是只根據提供的鏈上證據，分析多筆 cross-chain transaction 的安全風險。

必須遵守：
1. 只能使用輸入中提供的資料，不得假設未提供的地址聲譽、黑名單、漏洞情報、新聞事件或鏈下資訊。
2. 「交易已 EXECUTED」不等於「完全安全」；只能代表跨鏈流程已完成。
3. 若證據不足，必須明確寫出「證據不足」。
4. 分數範圍是 0-100，分數越高表示越安全，分數越低表示風險越高。
5. 請優先關注：
   - 跨鏈流程是否完整
   - 是否出現目的鏈執行失敗
   - 是否存在異常延遲或卡住
   - 來源鏈與目的鏈事件是否能合理對應
   - 是否存在明顯證據缺失或狀態矛盾
6. 你必須逐筆分析，不得把不同交易混在一起。
7. 回覆必須嚴格按照 TX-1、TX-2 ... 的順序輸出，不可漏掉交易，不可增減標題。

Verdict 規則：
- SAFE：70-100
- WARNING：40-69
- HIGH_RISK：0-39
- UNKNOWN：證據明顯不足

請用中文回答。"""

SECTION_HEADERS = ("結論", "風險分數", "主要風險", "判斷依據", "建議動作")
VALID_VERDICTS = {"SAFE", "WARNING", "HIGH_RISK", "UNKNOWN"}


@dataclass(frozen=True)
class RuleAssessment:
    """規則層產生的基線結果。"""

    verdict: str
    score: int
    factors: list[str]
    summary: str
    observations: list[str]


@dataclass(frozen=True)
class AIAssessment:
    """AI 成功解析後的單筆評審結果。"""

    verdict: str
    score: int
    factors: list[str]
    summary: str


@dataclass(frozen=True)
class BatchItem:
    """單筆交易在批量 prompt 中的輸入材料。"""

    canonical_id: str
    tx: XChainTx
    timelines: list[XChainTimelineEvent]
    rule: RuleAssessment
    prompt_block: str


@dataclass(frozen=True)
class RiskWorkerSnapshot:
    """風險 worker 的運行快照。"""

    running: bool
    pending_count: int
    last_error: str | None
    last_enqueued_ids: list[str]
    last_completed_ids: list[str]


class RiskService:
    """MVP 風險分析器。"""

    def __init__(self) -> None:
        """初始化背景 worker 與待分析隊列。"""
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_queue: deque[str] = deque()
        self._pending_set: set[str] = set()
        self._running = False
        self._last_error: str | None = None
        self._last_enqueued_ids: list[str] = []
        self._last_completed_ids: list[str] = []
        self._ai_backoff_until = 0.0

    def start(self) -> None:
        """啟動背景風險評審 worker。"""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="xchain-risk-worker", daemon=True)
        self._thread.start()
        logger.info("Risk worker started")

    def stop(self) -> None:
        """停止背景風險評審 worker。"""
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Risk worker stopped")

    def snapshot(self) -> RiskWorkerSnapshot:
        """返回背景 worker 的當前狀態。"""
        with self._lock:
            return RiskWorkerSnapshot(
                running=self._running,
                pending_count=len(self._pending_queue),
                last_error=self._last_error,
                last_enqueued_ids=list(self._last_enqueued_ids),
                last_completed_ids=list(self._last_completed_ids),
            )

    def analyze_transactions(self, db: Session, canonical_ids: list[str]) -> list[str]:
        """將交易加入待檢查池，由背景 worker 異步處理。"""
        del db
        if not canonical_ids:
            return []

        enqueued_ids: list[str] = []
        with self._condition:
            for canonical_id in canonical_ids:
                if canonical_id in self._pending_set:
                    continue
                self._pending_set.add(canonical_id)
                self._pending_queue.append(canonical_id)
                enqueued_ids.append(canonical_id)

            if enqueued_ids:
                self._last_enqueued_ids = list(enqueued_ids)
                self._condition.notify_all()

        return enqueued_ids

    def _run_loop(self) -> None:
        """背景主循環：從待檢查池取交易並執行批量分析。"""
        with self._lock:
            self._running = True

        try:
            while not self._stop_event.is_set():
                canonical_ids = self._dequeue_pending_ids()
                if not canonical_ids:
                    continue

                try:
                    completed_ids = self._process_pending_ids(canonical_ids)
                    with self._lock:
                        self._last_completed_ids = completed_ids
                        self._last_error = None
                except Exception as exc:
                    logger.exception("Risk worker batch failed: %s", exc)
                    with self._lock:
                        self._last_error = str(exc)
        finally:
            with self._lock:
                self._running = False

    def _dequeue_pending_ids(self) -> list[str]:
        """從待檢查池取出一批交易 id。"""
        with self._condition:
            while not self._stop_event.is_set() and not self._pending_queue:
                self._condition.wait(timeout=1)

            if not self._pending_queue:
                return []

            batch: list[str] = []
            max_take = max(1, settings.ai_batch_max_size)
            while self._pending_queue and len(batch) < max_take:
                canonical_id = self._pending_queue.popleft()
                self._pending_set.discard(canonical_id)
                batch.append(canonical_id)
            return batch

    def _process_pending_ids(self, canonical_ids: list[str]) -> list[str]:
        """對一批待評審交易做規則與 AI 分析並落庫。"""
        with SessionLocal() as db:
            tx_stmt = (
                select(XChainTx)
                .where(XChainTx.canonical_id.in_(canonical_ids))
                .order_by(desc(XChainTx.updated_at), XChainTx.canonical_id.asc())
            )
            txs = db.execute(tx_stmt).scalars().all()
            if not txs:
                return []

            ordered_ids = [tx.canonical_id for tx in txs]
            timelines_by_id = self._load_timelines(db, ordered_ids)
            batch_items = [
                self._build_batch_item(tx, timelines_by_id.get(tx.canonical_id, []))
                for tx in txs
            ]

            assessments: dict[str, AIAssessment | RuleAssessment] = {
                item.canonical_id: item.rule for item in batch_items
            }

            if settings.ai_api_key:
                for batch in self._build_batches(batch_items):
                    batch_canonical_ids = [item.canonical_id for item in batch]
                    ai_output = self._ai_assessment(batch)
                    if ai_output is None:
                        logger.warning(
                            "AI batch parse skipped: batch_size=%s canonical_ids=%s reason=no_ai_output",
                            len(batch),
                            batch_canonical_ids,
                        )
                        continue
                    parsed = self._parse_batch_response(ai_output, batch)
                    parsed_ids = [item.canonical_id for item in batch if item.canonical_id in parsed]
                    fallback_ids = [item.canonical_id for item in batch if item.canonical_id not in parsed]
                    logger.info(
                        "AI batch parsed: batch_size=%s parsed=%s fallback=%s parsed_ids=%s fallback_ids=%s",
                        len(batch),
                        len(parsed_ids),
                        len(fallback_ids),
                        parsed_ids,
                        fallback_ids,
                    )
                    for item in batch:
                        assessment = parsed.get(item.canonical_id)
                        if assessment is not None:
                            assessments[item.canonical_id] = assessment

            completed_ids: list[str] = []
            ai_applied_ids: list[str] = []
            rule_fallback_ids: list[str] = []
            for item in batch_items:
                assessment = assessments[item.canonical_id]
                ai_applied = isinstance(assessment, AIAssessment)
                if isinstance(assessment, AIAssessment):
                    ai_applied_ids.append(item.canonical_id)
                else:
                    rule_fallback_ids.append(item.canonical_id)
                self._upsert_report(
                    db,
                    item.canonical_id,
                    assessment.verdict,
                    assessment.score,
                    assessment.factors,
                    assessment.summary,
                    ai_applied=ai_applied,
                )
                completed_ids.append(item.canonical_id)

            db.commit()
            logger.info(
                "Risk reports committed: total=%s ai_applied=%s rule_fallback=%s ai_ids=%s fallback_ids=%s",
                len(completed_ids),
                len(ai_applied_ids),
                len(rule_fallback_ids),
                ai_applied_ids,
                rule_fallback_ids,
            )
            return completed_ids

    def _load_timelines(self, db: Session, canonical_ids: list[str]) -> dict[str, list[XChainTimelineEvent]]:
        """批量載入所有交易的時間線事件。"""
        if not canonical_ids:
            return {}

        stmt = (
            select(XChainTimelineEvent)
            .where(XChainTimelineEvent.canonical_id.in_(canonical_ids))
            .order_by(
                XChainTimelineEvent.canonical_id.asc(),
                XChainTimelineEvent.block_number.asc(),
                XChainTimelineEvent.log_index.asc(),
                XChainTimelineEvent.id.asc(),
            )
        )
        rows = db.execute(stmt).scalars().all()
        output: dict[str, list[XChainTimelineEvent]] = {canonical_id: [] for canonical_id in canonical_ids}
        for row in rows:
            output.setdefault(row.canonical_id, []).append(row)
        return output

    def _build_batch_item(self, tx: XChainTx, timelines: list[XChainTimelineEvent]) -> BatchItem:
        """組裝單筆交易的批量輸入塊。"""
        rule = self.build_rule_assessment(tx, timelines)
        prompt_block = self._build_tx_prompt_block(tx, timelines, rule)
        return BatchItem(
            canonical_id=tx.canonical_id,
            tx=tx,
            timelines=timelines,
            rule=rule,
            prompt_block=prompt_block,
        )

    def build_rule_assessment(self, tx: XChainTx, timelines: list[XChainTimelineEvent]) -> RuleAssessment:
        """基於狀態、失敗分類與時間線完整性產生規則判定。"""
        factors: list[str] = []
        observations: list[str] = []
        verdict = "SAFE"
        score = 90

        observations.append(f"交易狀態={tx.status}")
        observations.append(f"failure_category={tx.failure_category or 'NONE'}")

        if tx.src_chain_id is None or tx.dst_chain_id is None:
            observations.append("雙邊鏈路證據不完整")
            factors.append("來源鏈或目的鏈資料缺失")
            verdict = "UNKNOWN"
            score = min(score, 45)

        if not timelines:
            observations.append("無時間線事件")
            if "時間線事件缺失" not in factors:
                factors.append("時間線事件缺失")
            verdict = "UNKNOWN"
            score = min(score, 40)
        else:
            observations.append(f"timeline_events={len(timelines)}")

        if tx.status == "FAILED":
            verdict = "HIGH_RISK"
            score = min(score, 10)
            if "目的鏈執行失敗" not in factors:
                factors.append("目的鏈執行失敗")
        elif tx.status == "STUCK":
            verdict = "WARNING"
            score = min(score, 30)
            if tx.failure_category == "STUCK_NO_VERIFY":
                factors.append("長時間未進入驗證階段")
            elif tx.failure_category == "STUCK_NEED_EXECUTION":
                factors.append("已驗證但長時間未執行")
            else:
                factors.append("交易流程超時停滯")
        elif tx.status == "VERIFIED":
            verdict = "WARNING"
            score = min(score, 60)
            factors.append("交易仍在中間態，尚未完成執行")
        elif tx.status == "SENT":
            verdict = "WARNING"
            score = min(score, 70)
            factors.append("交易剛發送，需等待後續驗證或執行")
        elif tx.status == "EXECUTED":
            if verdict != "UNKNOWN":
                verdict = "SAFE"
                score = max(score, 95)
            factors.append("交易已完成執行")
        else:
            verdict = "UNKNOWN"
            score = min(score, 50)
            factors.append("狀態未知，需人工核對")

        if tx.latency_ms_total is not None:
            observations.append(f"total_latency_ms={tx.latency_ms_total}")
            if tx.latency_ms_total >= 3_600_000:
                factors.append("總延遲超過 1 小時")
                score = min(score, 55)
                if verdict == "SAFE":
                    verdict = "WARNING"

        summary = f"規則判定結果：{verdict}，分數 {score}。"
        return RuleAssessment(
            verdict=verdict,
            score=score,
            factors=self._dedupe_preserve_order(factors),
            summary=summary,
            observations=self._dedupe_preserve_order(observations),
        )

    def _build_batches(self, items: list[BatchItem]) -> list[list[BatchItem]]:
        """按批大小與 prompt 長度限制切分交易。"""
        if not items:
            return []

        target_batch_size = max(1, min(settings.ai_batch_size, settings.ai_batch_max_size))
        max_prompt_chars = max(1, settings.ai_max_prompt_chars)
        batches: list[list[BatchItem]] = []
        current: list[BatchItem] = []

        for item in items:
            candidate = current + [item]
            if len(candidate) > target_batch_size or self._batch_prompt_length(candidate) > max_prompt_chars:
                if current:
                    batches.append(current)
                    current = [item]
                else:
                    batches.append([item])
                    current = []
                continue
            current = candidate

        if current:
            batches.append(current)
        return batches

    def _batch_prompt_length(self, items: list[BatchItem]) -> int:
        """估算完整 prompt 長度，用於批次切分。"""
        return len(SYSTEM_PROMPT) + len(self._build_batch_user_prompt(items)) + 128

    def _build_batch_user_prompt(self, items: list[BatchItem]) -> str:
        """將一批交易組裝為 user prompt。"""
        parts = [
            f"請分析以下 {len(items)} 筆跨鏈交易。",
            "",
            "輸出要求：",
            "- 你必須逐筆輸出 TX-1 到 TX-N。",
            "- 每個 TX 區塊都必須包含：canonical_id、結論、風險分數、主要風險、判斷依據、建議動作。",
            "- 不可省略任何一筆交易。",
            "",
        ]
        for index, item in enumerate(items, start=1):
            parts.append(f"TX-{index}")
            parts.append(item.prompt_block)
            parts.append("")
        return "\n".join(parts).strip()

    def _build_tx_prompt_block(
        self,
        tx: XChainTx,
        timelines: list[XChainTimelineEvent],
        rule: RuleAssessment,
    ) -> str:
        """生成單筆交易在 prompt 中的固定文本。"""
        timeline_lines = self._timeline_lines(timelines)
        observation_lines = [f"- {item}" for item in rule.observations] or ["- 無"]
        return "\n".join(
            [
                f"canonical_id: {tx.canonical_id}",
                "",
                "[交易基本信息]",
                f"- protocol: {tx.protocol}",
                f"- status: {tx.status}",
                f"- failure_category: {tx.failure_category or 'NONE'}",
                f"- src_chain_id: {tx.src_chain_id}",
                f"- dst_chain_id: {tx.dst_chain_id}",
                f"- src_tx_hash: {tx.src_tx_hash}",
                f"- dst_tx_hash: {tx.dst_tx_hash}",
                f"- latency_ms_total: {tx.latency_ms_total}",
                f"- latency_ms_verify: {tx.latency_ms_verify}",
                f"- latency_ms_execute: {tx.latency_ms_execute}",
                f"- updated_at: {tx.updated_at.isoformat() if tx.updated_at else None}",
                "",
                "[規則層觀察]",
                *observation_lines,
                "",
                "[時間線]",
                *timeline_lines,
            ]
        )

    def _timeline_lines(self, timelines: list[XChainTimelineEvent]) -> list[str]:
        """將時間線事件轉為可供模型閱讀的行列表。"""
        if not timelines:
            return ["- 無時間線事件"]

        lines: list[str] = []
        for index, event in enumerate(timelines, start=1):
            lines.append(
                (
                    f"{index}. stage={event.stage}; chain_id={event.chain_id}; "
                    f"tx_hash={event.tx_hash}; block_number={event.block_number}; "
                    f"event_name={event.event_name}; event_ts={event.event_ts.isoformat() if event.event_ts else None}"
                )
            )
        return lines

    def _ai_assessment(self, items: list[BatchItem]) -> str | None:
        """對一個批次交易調用 AI，返回原始文本。"""
        if not settings.ai_api_key:
            return None

        user_prompt = self._build_batch_user_prompt(items)
        canonical_ids = [item.canonical_id for item in items]
        payload = {
            "model": settings.ai_model,
            "temperature": settings.ai_temperature,
            "max_tokens": settings.ai_max_output_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
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

        for attempt in range(1, 3):
            self._sleep_if_ai_backoff_active()
            logger.info(
                "AI batch request start: attempt=%s batch_size=%s canonical_ids=%s model=%s",
                attempt,
                len(items),
                canonical_ids,
                settings.ai_model,
            )
            try:
                with urllib.request.urlopen(req, timeout=settings.ai_timeout_seconds) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                logger.warning(
                    "AI batch request failed: attempt=%s batch_size=%s canonical_ids=%s status=%s error=%s body=%s",
                    attempt,
                    len(items),
                    canonical_ids,
                    exc.code,
                    exc,
                    json.dumps(body, ensure_ascii=False),
                )
                self._activate_ai_backoff()
                if attempt == 2:
                    return None
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                logger.warning(
                    "AI batch request failed: attempt=%s batch_size=%s canonical_ids=%s error=%s",
                    attempt,
                    len(items),
                    canonical_ids,
                    exc,
                )
                self._activate_ai_backoff()
                if attempt == 2:
                    return None
                continue

            content = self._extract_message_content(result)
            if content is None:
                logger.warning(
                    "AI batch response missing usable content: attempt=%s batch_size=%s canonical_ids=%s",
                    attempt,
                    len(items),
                    canonical_ids,
                )
                self._activate_ai_backoff()
                if attempt == 2:
                    return None
                continue

            logger.info(
                "AI batch request success: batch_size=%s canonical_ids=%s content=%s",
                len(items),
                canonical_ids,
                json.dumps(content, ensure_ascii=False),
            )
            return content

        return None

    def _sleep_if_ai_backoff_active(self) -> None:
        """若 AI 退避時間尚未結束，先等待後再重試。"""
        wait_seconds = max(0.0, self._ai_backoff_until - time.monotonic())
        if wait_seconds <= 0:
            return
        logger.warning("AI backoff active: wait_seconds=%.2f", wait_seconds)
        time.sleep(wait_seconds)

    def _activate_ai_backoff(self) -> None:
        """發生 AI 錯誤後啟用固定退避窗口。"""
        self._ai_backoff_until = time.monotonic() + AI_RETRY_BACKOFF_SECONDS

    def _extract_message_content(self, result: dict) -> str | None:
        """從 OpenAI-compatible 回應中提取文字內容。"""
        choices = result.get("choices", [])
        if not choices:
            return None

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip() or None
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
            joined = "\n".join(chunks).strip()
            return joined or None
        return None

    def _parse_batch_response(self, raw_text: str, items: list[BatchItem]) -> dict[str, AIAssessment]:
        """解析模型對一批交易的回覆。"""
        item_by_id = {item.canonical_id: item for item in items}
        parsed_sections = self._split_tx_sections(raw_text)
        output: dict[str, AIAssessment] = {}

        for section in parsed_sections:
            assessment = self._parse_single_section(section, item_by_id)
            if assessment is None:
                continue
            canonical_id, ai_assessment = assessment
            if canonical_id not in output:
                output[canonical_id] = ai_assessment

        return output

    def _split_tx_sections(self, raw_text: str) -> list[str]:
        """按 `TX-N` 頭部分割模型回覆。"""
        matches = list(re.finditer(r"(?m)^TX-(\d+)\s*:?\s*$", raw_text))
        if not matches:
            return []

        sections: list[str] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
            sections.append(raw_text[start:end].strip())
        return sections

    def _parse_single_section(
        self,
        section: str,
        item_by_id: dict[str, BatchItem],
    ) -> tuple[str, AIAssessment] | None:
        """解析單個 `TX-N` 區塊。"""
        normalized_section = self._normalize_section_text(section)

        canonical_match = re.search(r"(?m)^canonical_id:\s*(.+?)\s*$", normalized_section)
        if canonical_match is None:
            return None
        canonical_id = canonical_match.group(1).strip()
        if canonical_id not in item_by_id:
            return None

        verdict_text = self._extract_named_field(normalized_section, "結論")
        score_text = self._extract_named_field(normalized_section, "風險分數")
        risks_body = self._extract_named_block(normalized_section, "主要風險")
        evidence_body = self._extract_named_block(normalized_section, "判斷依據")
        actions_body = self._extract_named_block(normalized_section, "建議動作")

        if not verdict_text or not score_text or not risks_body or not evidence_body or not actions_body:
            return None

        verdict = verdict_text.strip().upper()
        if verdict not in VALID_VERDICTS:
            return None

        try:
            score = int(score_text.strip())
        except ValueError:
            return None
        if score < 0 or score > 100:
            return None

        factors = self._extract_numbered_items(risks_body)
        if not factors:
            compact = self._compact_inline_text(risks_body)
            if compact:
                factors = [compact]
        if not factors:
            return None

        summary = section.strip()
        return (
            canonical_id,
            AIAssessment(
                verdict=verdict,
                score=score,
                factors=self._dedupe_preserve_order(factors),
                summary=summary,
            ),
        )

    def _extract_named_field(self, section: str, name: str) -> str | None:
        """提取單行欄位，兼容 `標題: 值` 與下一行取值。"""
        inline_pattern = rf"(?m)^{re.escape(name)}:\s*(.+?)\s*$"
        inline_match = re.search(inline_pattern, section)
        if inline_match and inline_match.group(1).strip():
            return inline_match.group(1).strip()

        inline_pattern_loose = rf"(?m)^(?:[-*]\s*)?{re.escape(name)}\s*:\s*(.+?)\s*$"
        inline_match_loose = re.search(inline_pattern_loose, section)
        if inline_match_loose and inline_match_loose.group(1).strip():
            return inline_match_loose.group(1).strip()

        block = self._extract_named_block(section, name)
        if block is None:
            return None
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        return lines[0] if lines else None

    def _extract_named_block(self, section: str, name: str) -> str | None:
        """提取某個標題下到下一個標題前的文本塊。"""
        header_pattern = rf"(?m)^(?:[-*]\s*)?{re.escape(name)}:\s*$"
        header_match = re.search(header_pattern, section)
        if header_match is None:
            inline_pattern = rf"(?m)^(?:[-*]\s*)?{re.escape(name)}:\s*(.+?)\s*$"
            inline_match = re.search(inline_pattern, section)
            if inline_match is None:
                return self._extract_named_block_by_keyword(section, name)
            return inline_match.group(1).strip()

        start = header_match.end()
        end = len(section)
        for candidate in SECTION_HEADERS:
            if candidate == name:
                continue
            candidate_match = re.search(rf"(?m)^(?:[-*]\s*)?{re.escape(candidate)}:\s*.*$", section[start:])
            if candidate_match is None:
                continue
            end = min(end, start + candidate_match.start())
        return section[start:end].strip() or None

    def _extract_named_block_by_keyword(self, section: str, name: str) -> str | None:
        """在格式較亂時，僅憑關鍵字定位段落文本。"""
        marker = f"{name}:"
        start = section.find(marker)
        if start < 0:
            return None
        start += len(marker)
        end = len(section)
        for candidate in SECTION_HEADERS:
            if candidate == name:
                continue
            candidate_marker = f"\n{candidate}:"
            candidate_index = section.find(candidate_marker, start)
            if candidate_index >= 0:
                end = min(end, candidate_index)
        body = section[start:end].strip()
        return body or None

    def _extract_numbered_items(self, body: str) -> list[str]:
        """從 `1. xxx` 形式的文本中提取列表項。"""
        output: list[str] = []
        for line in body.splitlines():
            match = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
            if match is None:
                bullet_match = re.match(r"^\s*[-*]\s*(.+?)\s*$", line)
                if bullet_match is None:
                    continue
                value = bullet_match.group(1).strip()
            else:
                value = match.group(1).strip()
            if value:
                output.append(value)
        return output

    def _normalize_section_text(self, text: str) -> str:
        """標準化模型段落，兼容 markdown 粗體標題與分隔線。"""
        normalized = text.replace("\r\n", "\n")
        normalized = re.sub(r"(?m)^\s*\*{3,}\s*$", "", normalized)
        for header in SECTION_HEADERS:
            normalized = re.sub(
                rf"(?m)^\s*\*\*{re.escape(header)}:\*\*\s*(.*?)\s*$",
                rf"{header}: \1",
                normalized,
            )
            normalized = re.sub(
                rf"(?m)^\s*\*\*{re.escape(header)}\*\*\s*:\s*(.*?)\s*$",
                rf"{header}: \1",
                normalized,
            )
            normalized = re.sub(
                rf"(?m)^\s*\*\*{re.escape(header)}\*\*\s*$",
                rf"{header}:",
                normalized,
            )
        normalized = re.sub(r"(?m)^\s*-\s*(結論|風險分數|主要風險|判斷依據|建議動作)\s*:\s*", r"\1: ", normalized)
        return normalized.strip()

    def _compact_inline_text(self, text: str) -> str | None:
        """將單段文本壓縮成單行，用於非列表式風險點回退。"""
        compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
        return compact or None

    def _upsert_report(
        self,
        db: Session,
        canonical_id: str,
        verdict: str,
        score: int,
        factors: list[str],
        summary: str,
        ai_applied: bool,
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
        latest.ai_model = settings.ai_model if ai_applied and settings.ai_api_key else None
        latest.prompt_version = PROMPT_VERSION if ai_applied else None

    def _dedupe_preserve_order(self, items: list[str]) -> list[str]:
        """對列表做去重並保持原順序。"""
        output: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            output.append(item)
        return output


risk_service = RiskService()
