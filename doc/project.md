# 重點：
1. 同時顯示四種跨鏈協議的所有跨鏈交易
2. 規範各協議顯示方式
3. 使用數據規範（只限鏈上 or 考慮協議數據)
4. 使用多少鏈數據？
2個鏈
2個協議
5. 時間範圍 (核心是要監控，要持續更新)
6. 核心：安全分析，要判斷交易是否安全 (去問 AI 是否安全)

---
## 1) 本 demo 實作目標

* 做一個單一 Explorer 介面
* 在你選定的鏈集合內，**同時顯示四種跨鏈協議的所有跨鏈交易**
* 對每筆跨鏈交易提供

  * 統一時間線 Timeline
  * 統一狀態 Status
  * 統一失敗歸因 Failure category
  * 延遲分解 Latency breakdown
* 支援查找方式

  * 以 `txHash` 查
  * 以 `canonicalId` 查
  * 以 `address` 查（若只用 onchain 會有覆蓋限制，後面會說）

差異化點建議

* 「跨協議統一時間線」是主差異化
* 每筆交易都能展開看鏈上事件證據
* 失敗不只顯示 Failed，還會顯示卡在哪一段和可能原因

---

## 2) Demo 檢測的協議範圍與簡介

### A) LayerZero V2

* 定位：跨鏈 messaging protocol
* 重要特徵：Endpoint 合約將流程拆成 `send -> verify -> lzReceive`
* onchain 可觀測事件與狀態

  * `PacketSent` 在源鏈 emit
  * `PacketVerified` 在目的鏈 emit
  * `PacketDelivered` 在目的鏈 emit ([GitHub][1])
* GUID：Endpoint 在構造 packet 時會生成 `guid` 並用於追蹤 ([GitHub][1])

### B) Axelar GMP

* 定位：跨鏈合約呼叫與跨鏈執行
* 核心流程：源鏈 `callContract` 觸發 `ContractCall`，目的鏈 gateway 記錄批准並 emit `ContractCallApproved`，再由 relayer 觸發 execute ([Axelar Documentation][2])
* onchain 可觀測事件與狀態

  * `ContractCall`
  * `ContractCallApproved`
  * `Executed(commandId)` ([GitHub][3])

### C) Wormhole Token Bridge

* 定位：以 VAA 為 proof 的跨鏈 token transfer
* 核心概念：VAA 將 message 與 Guardian signatures 組成 proof，並以 `(emitter_chain, emitter_address, sequence)` 唯一索引 ([Wormhole][4])
* WTT（Token Bridge）採用 lock-and-mint 或 release-or-mint 的思路 ([Wormhole][5])
* onchain 可觀測事件與狀態

  * Core 合約 `LogMessagePublished(sender, sequence, ...)` ([GitHub][6])
  * TokenBridge 合約 `TransferRedeemed(emitterChainId, emitterAddress, sequence)` ([GitHub][7])

### D) Chainlink CCIP

* 定位：跨鏈 message 和 token transfer
* 架構：offchain DON 做 commit 與 execute，onchain 有 Router OnRamp OffRamp 等組件 ([Chainlink Documentation][8])
* onchain 可觀測事件與狀態（v1.6.1）

  * 源鏈 OnRamp `CCIPMessageSent`
  * 目的鏈 OffRamp `ExecutionStateChanged`
  * 目的鏈 Router `MessageExecuted` ([Chainlink Documentation][9])

---

## 3) 如何用統一方式展示四種協議的跨鏈交易

### 3.1 統一資料模型

建議定義一個內部統一物件 `XChainTx`，UI 永遠只吃這個結構

* `protocol`: layerzero | axelar | wormhole | ccip
* `canonicalId`: 跨鏈唯一 ID
* `src`: chain + txHash + timestamp
* `dst`: chain + txHash + timestamp（若已完成）
* `status`: 統一狀態
* `timeline[]`: 統一時間線節點
* `failure`: 統一失敗歸因

### 3.2 統一狀態與時間線

統一狀態建議只保留 5 個

* `SENT`：源鏈已發出跨鏈請求
* `VERIFIED`：目的鏈已驗證或已批准
* `EXECUTED`：目的鏈已投遞或已執行
* `FAILED`：鏈上可觀測失敗
* `STUCK`：超時仍未進入下一階段

對應映射（可直接落地）

* LayerZero V2

  * `PacketSent -> SENT`
  * `PacketVerified -> VERIFIED`
  * `PacketDelivered -> EXECUTED` ([GitHub][1])
* Axelar GMP

  * `ContractCall -> SENT`
  * `ContractCallApproved -> VERIFIED`
  * `Executed -> EXECUTED` ([GitHub][3])
* Wormhole Token Bridge

  * `LogMessagePublished -> SENT` ([GitHub][6])
  * `TransferRedeemed -> EXECUTED` ([GitHub][7])
* CCIP

  * `CCIPMessageSent -> SENT`
  * `ExecutionStateChanged(SUCCESS) -> EXECUTED`
  * `ExecutionStateChanged(FAILURE) -> FAILED`
  * `MessageExecuted` 可作為 executed 的強信號 ([Chainlink Documentation][9])

### 3.3 統一查找與瀏覽方式

* Search box 支援三種 key

  * `txHash`：最實用也最容易 onchain-only 支援
  * `canonicalId`：跨鏈唯一 id
  * `address`：若要做好，通常需要 indexer 或協議 API 補強
* Browse 頁面

  * Latest crosschain tx stream
  * 依協議 tab 分組
  * 依狀態分組
  * 依鏈對分組

---

## 4) 數據來源收集方式

### 4.1 只靠 onchain data 的方法

你需要兩類資料

* RPC：`eth_getLogs` 和 `getTransactionReceipt`
* 合約地址與 ABI：EndpointV2，AxelarGateway，Wormhole core，Wormhole token bridge，CCIP Router 相關合約

onchain-only 的優點

* 完全可驗證
* 不依賴協議服務穩定性
* 更像研究型 demo

onchain-only 的限制

* 很多「中間態」本來就發生在鏈下網路
* address 搜索在 LayerZero 和 CCIP 可能不完整

  * 因為關鍵事件未必把 sender 作為 indexed topic

onchain-only 的最低可行抓取點

* LayerZero V2：監聽 EndpointV2 的 `PacketSent PacketVerified PacketDelivered` ([GitHub][1])
* Axelar：監聽 gateway 的 `ContractCall ContractCallApproved Executed` ([GitHub][3])
* Wormhole：監聽 core 的 `LogMessagePublished` 和 token bridge 的 `TransferRedeemed` ([GitHub][6])
* CCIP：監聽 `CCIPMessageSent` 和 `ExecutionStateChanged` 和 `MessageExecuted` ([Chainlink Documentation][9])

### 4.2 包括協議 API data 的方法

用 API 的目的不是替代鏈上證據，而是補齊查詢能力與中間態。

* LayerZero Scan API

  * 支援依 `tx` `guid` `wallet` 查 message ([scan.layerzero-api.com][10])
* Axelarscan GMP API

  * 提供狀態更新與 gas 相關資訊 ([docs.axelarscan.io][11])
* Wormholescan API

  * 有 `/api/v1` 的 explorer API，並提供 `operations?address=` 的歷史查詢 ([docs.wormholescan.io][12])
* CCIP Explorer 與 offchain status

  * Explorer 可用 Message ID 追蹤狀態 ([Chainlink Documentation][13])
  * 官方也提供 offchain 查狀態的教學 ([Chainlink Documentation][14])

---

## 5) 具體可行方案與實作內容清單

### 5.1 你要實作的系統模組

* [ ] Chain Registry

  * 支援鏈列表
  * RPC endpoint
  * chain id 或 selector
* [ ] Protocol Registry

  * 每條鏈上各協議核心合約地址
  * ABI 與 event topic 定義
  * CCIP 的 OnRamp 建議從 Router 取得，避免 hardcode ([Chainlink Documentation][15])
* [ ] Indexer（或按需查詢器）

  * onchain-only 模式
  * optional API enrich 模式
* [ ] Normalizer（最重要）

  * 把不同協議事件轉為 `XChainTx`
  * 實作 canonicalId 規則與狀態機
* [ ] Backend API

  * `/search?q=`
  * `/tx/{canonicalId}`
  * `/latest?protocol=`
* [ ] Frontend

  * 統一列表頁
  * 統一詳情頁
  * 統一時間線組件

### 5.2 canonicalId 與 join 規則

* LayerZero V2

  * 優先用 guid
  * PacketSent 事件只有 `encodedPacket`，你需要 decode 取得 nonce srcEid dstEid sender receiver guid ([GitHub][1])
  * 若 decode 成本太高，先用 `(srcEid, sender, dstEid, receiver, nonce)` 作為臨時 canonicalId
* Axelar GMP

  * 用 `commandId` 最穩
  * `ContractCallApproved` 事件直接帶 `commandId` 與 `payloadHash` 與 `sourceTxHash` ([GitHub][3])
* Wormhole Token Bridge

  * 用 `(emitterChainId, emitterAddress, sequence)`
  * 這也是 VAA 的官方唯一索引 ([Wormhole][4])
* CCIP

  * 用 `messageId`
  * 事件參考裡明確列出 `ExecutionStateChanged` 帶 `messageId`，Router 也會 emit `MessageExecuted(messageId)` ([Chainlink Documentation][9])

### 5.3 失敗歸因規則

定義少量可解釋的類別就夠 demo

* `FAILED_DEST_EXECUTION`：目的鏈執行失敗

  * CCIP 可用 `ExecutionStateChanged(FAILURE)` ([Chainlink Documentation][9])
* `STUCK_NO_VERIFY`：看到源鏈事件但超時未驗證
* `STUCK_NEED_EXECUTION`：已驗證或已批准但超時未執行

  * Axelar 文檔描述批准後會由 relayer 觸發 execute，你可以用超時推斷卡在這裡 ([Axelar Documentation][2])
* `UNKNOWN`：其他情況

### 5.4 建議的落地順序

* [ ] 第 1 步：先做 `txHash` 查詢

  * `getTransactionReceipt(txHash)`
  * 解析 logs
  * 若命中任一協議事件就生成一筆 `XChainTx`
* [ ] 第 2 步：做 latest stream

  * 用 `eth_getLogs` 按區塊掃描四類合約事件
  * 存 SQLite 或 Postgres
* [ ] 第 3 步：做 detail join

  * 對每筆 `SENT`，再去目的鏈掃對應事件並更新狀態
* [ ] 第 4 步：加 API enrich 開關

  * 在 UI 上顯示「鏈上證據」和「協議 API 補充」兩個欄位
  * 用於補 address 搜索與中間態


[1]: https://raw.githubusercontent.com/LayerZero-Labs/LayerZero-v2/main/packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol "raw.githubusercontent.com"
[2]: https://docs.axelar.dev/dev/general-message-passing/overview/?utm_source=chatgpt.com "General Message Passing"
[3]: https://raw.githubusercontent.com/axelarnetwork/axelar-cgp-solidity/main/contracts/interfaces/IAxelarGateway.sol "raw.githubusercontent.com"
[4]: https://wormhole.com/docs/protocol/infrastructure/vaas/?utm_source=chatgpt.com "VAAs | Wormhole Docs"
[5]: https://wormhole.com/docs/products/token-transfers/wrapped-token-transfers/overview/?utm_source=chatgpt.com "Wrapped Token Transfers (WTT) Overview | Wormhole Docs"
[6]: https://raw.githubusercontent.com/wormhole-foundation/wormhole/main/ethereum/contracts/interfaces/IWormhole.sol "raw.githubusercontent.com"
[7]: https://raw.githubusercontent.com/wormhole-foundation/wormhole/main/ethereum/contracts/bridge/interfaces/ITokenBridge.sol "raw.githubusercontent.com"
[8]: https://docs.chain.link/ccip/concepts/architecture/overview "CCIP Architecture - Overview | Chainlink Documentation"
[9]: https://docs.chain.link/ccip/api-reference/evm/v1.6.1/events "CCIP v1.6.1 Events API Reference | Chainlink Documentation"
[10]: https://scan.layerzero-api.com/v1/swagger?utm_source=chatgpt.com "LayerZero Scan API 1.0.0 OAS 3.0"
[11]: https://docs.axelarscan.io/gmp?utm_source=chatgpt.com "GMP API - API Reference"
[12]: https://docs.wormholescan.io/?utm_source=chatgpt.com "Wormholescan API"
[13]: https://docs.chain.link/ccip/tools-resources/ccip-explorer?utm_source=chatgpt.com "CCIP Explorer"
[14]: https://docs.chain.link/ccip/tutorials/evm/offchain/get-status-offchain?utm_source=chatgpt.com "Checking CCIP Message Status"
[15]: https://docs.chain.link/ccip/concepts/architecture/onchain/evm/components "Onchain Architecture - Components (EVM) | Chainlink Documentation"
