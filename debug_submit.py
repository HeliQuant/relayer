"""Debug script — verify our EIP-712 digest matches the contract's."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

import json

MANTLE_SEPOLIA_RPC = os.environ["MANTLE_SEPOLIA_RPC_URL"]
PUBLISHER_PRIVATE_KEY = os.environ["DEPLOYER_PRIVATE_KEY"]
ALLORA_CONSUMER_ADDR = Web3.to_checksum_address(os.environ["ALLORA_CONSUMER_ADDRESS"])

ABI_PATH = ROOT / "contracts" / "out" / "AlloraConsumer.sol" / "AlloraConsumer.json"

w3 = Web3(Web3.HTTPProvider(MANTLE_SEPOLIA_RPC))
with ABI_PATH.open() as f:
    abi = json.load(f)["abi"]
consumer = w3.eth.contract(address=ALLORA_CONSUMER_ADDR, abi=abi)
account = Account.from_key(PUBLISHER_PRIVATE_KEY)

print("Deployer (signer):", account.address)
print("Consumer:", ALLORA_CONSUMER_ADDR)
print("Chain id:", w3.eth.chain_id)
print("isTopicEnabled[14]:", consumer.functions.isTopicEnabled(14).call())
print("trustedSigners[me]:", consumer.functions.trustedSigners(account.address).call())
print("maxFreshnessSeconds:", consumer.functions.maxFreshnessSeconds().call())
print("block.timestamp:", w3.eth.get_block("latest").timestamp)
print()

# Build test payload
now = int(time.time())
test_data = (14, 74_900_000000000000000000, now, 7500, b"")  # (topicId, networkInf, ts, conf, extra)

# 1) Get contract-computed digest
contract_digest = consumer.functions.digestFor(test_data).call()
print(f"Contract digest:  {contract_digest.hex()}")

# 2) Compute locally via eth_account
typed = {
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
        "chainId": w3.eth.chain_id,
        "verifyingContract": ALLORA_CONSUMER_ADDR,
    },
    "message": {
        "topicId": 14,
        "networkInference": 74_900_000000000000000000,
        "networkInferenceTimestamp": now,
        "confidenceBps": 7500,
        "extraData": b"",
    },
}
encoded = encode_typed_data(full_message=typed)
print(f"Local message hash: {encoded.body.hex()}")
print(f"Local domain hash:  {encoded.header.hex()}")
# Total digest = keccak256("\x19\x01" + domainSeparator + structHash)
import hashlib
from eth_utils import keccak
total = keccak(b"\x19\x01" + encoded.header + encoded.body)
print(f"Local total digest: 0x{total.hex()}")
print()
print(f"Match? {('YES' if total.hex() == contract_digest.hex().lstrip('0x') else 'NO').upper()}")

# Sign locally + recover
signed = Account.sign_message(encoded, private_key=PUBLISHER_PRIVATE_KEY)
print(f"Signature: 0x{signed.signature.hex()}")

# Try recover
from eth_account._utils.signing import recover_pubkey
recovered = Account.recover_message(encoded, signature=signed.signature)
print(f"Recovered signer: {recovered}")
print(f"Match deployer? {recovered.lower() == account.address.lower()}")
