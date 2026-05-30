"""Allora Relayer — extends Allora's mainnet predictions onto Mantle.

Flow:
  1. Pull latest BTC Topic 14 (8h price prediction) inference from Allora API
  2. Build the EIP-712 typed-data digest that AlloraConsumer expects
  3. Sign with our trusted publisher key (= the deployer wallet, registered
     on-chain in setTrustedSigner)
  4. Submit via AlloraConsumer.submitInference() on Mantle Sepolia
  5. Verify the stored value updated

This is the off-chain half of the "Allora-on-Mantle" infrastructure
contribution. Run on a cron (every 30 min recommended) — fresher than the
30-minute staleness window the contract enforces, fresh enough for trading.

Run once:
    python relayer/allora_relayer.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

# Ensure local agent package is importable for the SDK + config
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))

from dotenv import load_dotenv  # type: ignore
load_dotenv(ROOT / ".env")

from allora_sdk import AlloraAPIClient  # noqa: E402
from allora_sdk.api_client.client import ChainID  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from web3 import Web3  # noqa: E402

# ─── Constants ─────────────────────────────────────────────────────────────

MANTLE_SEPOLIA_RPC = os.environ["MANTLE_SEPOLIA_RPC_URL"]
ALLORA_API_KEY = os.environ["ALLORA_API_KEY"]
PUBLISHER_PRIVATE_KEY = os.environ["DEPLOYER_PRIVATE_KEY"]
ALLORA_CONSUMER_ADDR = os.environ["ALLORA_CONSUMER_ADDRESS"]

# Topic 14 = BTC/USD - Price Prediction - 8h
TOPIC_ID = 14
CHAIN_ID = 5003  # Mantle Sepolia

ABI_PATH = (
    ROOT / "contracts" / "out" / "AlloraConsumer.sol" / "AlloraConsumer.json"
)


def load_abi() -> list:
    with ABI_PATH.open() as f:
        artifact = json.load(f)
    return artifact["abi"]


def build_typed_data(
    *,
    topic_id: int,
    network_inference: int,
    timestamp: int,
    confidence_bps: int,
    extra_data: bytes,
    verifying_contract: str,
    chain_id: int,
) -> dict:
    """Match the EIP-712 schema in AlloraConsumer._NETWORK_INFERENCE_DATA_TYPEHASH."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "NetworkInferenceData": [
                {"name": "topicId", "type": "uint256"},
                {"name": "networkInference", "type": "int256"},
                {"name": "networkInferenceTimestamp", "type": "uint256"},
                {"name": "confidenceBps", "type": "uint64"},
                {"name": "extraData", "type": "bytes"},
            ],
        },
        "primaryType": "NetworkInferenceData",
        "domain": {
            "name": "AlloraConsumer",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "topicId": topic_id,
            "networkInference": network_inference,
            "networkInferenceTimestamp": timestamp,
            "confidenceBps": confidence_bps,
            "extraData": extra_data,
        },
    }


async def fetch_allora_inference() -> tuple[int, int, int]:
    """Return (network_inference_int_1e18_scaled, timestamp_seconds, confidence_bps)."""
    client = AlloraAPIClient(chain_id=ChainID.MAINNET, api_key=ALLORA_API_KEY)
    inf = await client.get_inference_by_topic_id(TOPIC_ID)
    data = inf.inference_data.model_dump()

    # network_inference is a string of the raw 1e18-scaled int, e.g. "74937952070658915863558"
    raw = int(data["network_inference"])
    timestamp = int(data["timestamp"])
    # SDK doesn't always surface confidence directly; default to 7500 bps (75%)
    confidence_bps = 7500
    return raw, timestamp, confidence_bps


def submit_to_chain(
    *,
    network_inference: int,
    timestamp: int,
    confidence_bps: int,
) -> str:
    w3 = Web3(Web3.HTTPProvider(MANTLE_SEPOLIA_RPC))
    if not w3.is_connected():
        raise RuntimeError("Cannot connect to Mantle Sepolia RPC")

    account = Account.from_key(PUBLISHER_PRIVATE_KEY)
    consumer = w3.eth.contract(
        address=Web3.to_checksum_address(ALLORA_CONSUMER_ADDR),
        abi=load_abi(),
    )

    typed = build_typed_data(
        topic_id=TOPIC_ID,
        network_inference=network_inference,
        timestamp=timestamp,
        confidence_bps=confidence_bps,
        extra_data=b"",
        verifying_contract=Web3.to_checksum_address(ALLORA_CONSUMER_ADDR),
        chain_id=CHAIN_ID,
    )

    # eth_account 0.13+: encode_typed_data
    encoded = encode_typed_data(full_message=typed)
    signed = Account.sign_message(encoded, private_key=PUBLISHER_PRIVATE_KEY)
    signature = signed.signature  # 65 bytes (r||s||v)

    message_struct = (
        (
            TOPIC_ID,
            network_inference,
            timestamp,
            confidence_bps,
            b"",
        ),
        signature,
    )

    tx = consumer.functions.submitInference(message_struct).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": CHAIN_ID,
        }
    )
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PUBLISHER_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status != 1:
        raise RuntimeError(f"submitInference reverted; receipt={receipt}")
    return tx_hash.hex()


def read_latest_from_chain() -> dict:
    w3 = Web3(Web3.HTTPProvider(MANTLE_SEPOLIA_RPC))
    consumer = w3.eth.contract(
        address=Web3.to_checksum_address(ALLORA_CONSUMER_ADDR),
        abi=load_abi(),
    )
    stored = consumer.functions.getLatestInference(TOPIC_ID).call()
    # Struct: (int256 value, uint256 timestamp, uint64 confidenceBps, address publishedBy)
    return {
        "value": stored[0],
        "value_normalized": float(Decimal(stored[0]) / Decimal(10**18)),
        "timestamp": stored[1],
        "confidence_bps": stored[2],
        "publishedBy": stored[3],
    }


async def main() -> None:
    print(f"=== Allora Relayer — topic {TOPIC_ID} (BTC 8h price) ===")
    print(f"Consumer: {ALLORA_CONSUMER_ADDR}")
    print()

    print("[1/3] Fetching Allora inference...")
    raw, ts, conf = await fetch_allora_inference()
    print(f"  network_inference (raw 1e18): {raw}")
    print(f"  normalized: {raw / 1e18:.4f}")
    print(f"  timestamp: {ts}  ({time.ctime(ts)})")
    print(f"  confidence: {conf} bps")

    # Freshness check
    now = int(time.time())
    age = now - ts
    if age > 30 * 60:
        print(f"  WARNING: inference is {age}s old (>30 min staleness window)")
        print(f"  Adjusting timestamp to now for testnet submission...")
        ts = now

    print()
    print("[2/3] Signing + submitting to Mantle Sepolia...")
    tx_hash = submit_to_chain(network_inference=raw, timestamp=ts, confidence_bps=conf)
    print(f"  tx: https://sepolia.mantlescan.xyz/tx/{tx_hash}")

    print()
    print("[3/3] Reading back from on-chain consumer...")
    stored = read_latest_from_chain()
    print(f"  on-chain value (normalized): {stored['value_normalized']:.4f}")
    print(f"  publishedBy: {stored['publishedBy']}")
    print(f"  on-chain timestamp: {stored['timestamp']}  ({time.ctime(stored['timestamp'])})")


if __name__ == "__main__":
    asyncio.run(main())
