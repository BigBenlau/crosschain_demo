# 協議事件 Topic0（Ethereum Mainnet Prod）

## 說明
- 本文件列出本專案使用的 `LayerZero` 與 `Wormhole` 事件 `topic0`。
- `topic0` 由事件簽名做 `Keccak-256` 計算得到。
- 對 EVM 來說，事件簽名相同則 `topic0` 相同，因此可用於 Ethereum Mainnet 與其他 EVM 鏈。

## LayerZero（Endpoint V2）

### `SENT`
- Event signature: `PacketSent(bytes,bytes,address)`
- Topic0: `0x1ab700d4ced0c005b164c0f789fd09fcbb0156d4c2041b8a3bfbcd961cd1567f`

### `VERIFIED`
- Event signature: `PacketVerified((uint32,bytes32,uint64),address,bytes32)`
- Topic0: `0x0d87345f3d1c929caba93e1c3821b54ff3512e12b66aa3cfe54b6bcbc17e59b4`

### `EXECUTED`
- Event signature: `PacketDelivered((uint32,bytes32,uint64),address)`
- Topic0: `0x3cd5e48f9730b129dc7550f0fcea9c767b7be37837cd10e55eb35f734f4bca04`

### `FAILED`
- Event signature: `LzReceiveAlert(address,address,(uint32,bytes32,uint64),bytes32,uint256,uint256,bytes,bytes,bytes)`
- Topic0: `0x7edfa10fe10193301ad8a8bea7e968c7bcabcc64981f368e3aeada40ce26ae2c`

## Wormhole

### `SENT`（Core）
- Event signature: `LogMessagePublished(address,uint64,uint32,bytes,uint8)`
- Topic0: `0x6eb224fb001ed210e379b335e35efe88672a8ce935d981a6896b27ffdf52a3b2`

### `EXECUTED`（Token Bridge）
- Event signature: `TransferRedeemed(uint16,bytes32,uint64)`
- Topic0: `0xcaf280c8cfeba144da67230d9b009c8f868a75bac9a528fa0474be1ba317c169`

## 對應配置鍵
- `LAYERZERO_SENT_TOPICS`
- `LAYERZERO_VERIFIED_TOPICS`
- `LAYERZERO_EXECUTED_TOPICS`
- `LAYERZERO_FAILED_TOPICS`
- `WORMHOLE_SENT_TOPICS`
- `WORMHOLE_EXECUTED_TOPICS`

## 來源（官方合約/介面）
- LayerZero EndpointV2: `packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol`
- Wormhole Core (Ethereum): `ethereum/contracts/interfaces/IWormhole.sol`
- Wormhole Token Bridge (Ethereum): `ethereum/contracts/bridge/interfaces/ITokenBridge.sol`
