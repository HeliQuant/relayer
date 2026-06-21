# relayer

> Off-chain Allora Network → Mantle on-chain prediction bridge for **[HeliQuant](https://github.com/HeliQuant)**.

## What it does

HeliQuant uses Allora's decentralised AI inference network as one of four intelligence sources (the macro-direction signal). Allora publishes signed predictions on its own Cosmos-SDK chain — but those predictions aren't natively queryable from Mantle smart contracts.

This relayer bridges them:

1. **Pulls** the latest BTC 8h price prediction (Allora Topic 14) via the mainnet API
2. **Signs** an EIP-712 typed payload using a publisher key (registered as trusted on-chain)
3. **Submits** to `AlloraConsumer.submitInference(...)` on Mantle Sepolia
4. **Result**: any Mantle dApp can now call `getLatestInference(14)` to read the freshest Allora prediction

This is — as far as we are aware — the **first Allora prediction infrastructure live on Mantle**. The AlloraConsumer contract is part of HeliQuant's submission but designed to be reusable by any other Mantle project.

## Confirmed on-chain

**First successful submission**: [tx 0x0d7c09…c469](https://sepolia.mantlescan.xyz/tx/0x0d7c09c945f74595a484b16f185db5c78d175eb286596a881bc78868a6c745b1)

- AlloraConsumer: [`0x7A072465AC232709C114C5DAa842a9b7010D1d4f`](https://sepolia.mantlescan.xyz/address/0x7A072465AC232709C114C5DAa842a9b7010D1d4f)
- Inference value: BTC 8h price = $73,065.41
- Published by: `0x48379F4d1427209311E9FF0bcC4a354953ea631B` (trusted signer)

## Quickstart

```bash
# Requires the HeliQuant .env at repo root:
#   ALLORA_API_KEY=...          (free tier from developer.allora.network)
#   DEPLOYER_PRIVATE_KEY=0x...   (also acts as Allora publisher key)
#   MANTLE_SEPOLIA_RPC_URL=https://rpc.sepolia.mantle.xyz
#   ALLORA_CONSUMER_ADDRESS=0x7A07...1d4f

# Install deps in a shared agents venv (or your own)
pip install web3 eth-account allora_sdk python-dotenv requests

# Run a one-shot relay
python allora_relayer.py
```

## Production deployment

For continuous operation, run on a cron (every 30 min recommended — matches the contract's freshness window):

```cron
*/30 * * * * cd /path/to/relayer && /path/to/python allora_relayer.py >> /var/log/heliquant-relayer.log 2>&1
```

Or as a GitHub Actions workflow with secrets.

## Files

- `allora_relayer.py` — main relayer (production)
- `debug_submit.py` — EIP-712 digest debugger (development)

## License

MIT
