import asyncio
import json
import logging
from operator import itemgetter
import os
import sys
import time
from torch.utils.data import DataLoader


from web3 import Web3
from tinyfl.ipfs import load_model_ipfs

from tinyfl.model import models, scorers

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s:     %(message)s - %(asctime)s",
)
logger = logging.getLogger(__name__)

with open(sys.argv[1]) as f:
    config = json.load(f)
    (
        cur_model,
        scorer,
        endpoint,
        registration_contract_address,
        round_free_contract_address,
        ipfs_host,
        account,
    ) = itemgetter(
        "model",
        "scorer",
        "endpoint",
        "registration_contract_address",
        "round_free_contract_address",
        "ipfs_host",
        "account",
    )(
        config
    )
    model = models[cur_model]
    scorer = scorers[scorer]

logger.info(f"Model: {model.__name__}")
logger.info(f"Scorer: {scorer.__name__}")

w3 = Web3(Web3.HTTPProvider(endpoint))


_, testset = model.create_datasets()
testloader = DataLoader(testset, batch_size=64)
model = models[cur_model].create_model()

registration_contract = w3.eth.contract(
    address=Web3.to_checksum_address(registration_contract_address),
    abi=json.load(
        open(
            str(os.path.join(os.path.dirname(__file__), "../../abi/Registration.json"))
        )
    ),
)

round_free_contract = w3.eth.contract(
    address=Web3.to_checksum_address(round_free_contract_address),
    abi=json.load(
        open(str(os.path.join(os.path.dirname(__file__), "../../abi/RoundFree.json")))
    ),
)

registration_contract.functions.registerDevice("scorer").transact()

w3.eth.default_account = account


async def score_model(cid: str):
    print("it scorin time", cid)

    model.load_state_dict(await load_model_ipfs(cid, ipfs_host))
    accuracy, _ = model.test_model(testloader)
    round_free_contract.functions.scoreModel(cid, accuracy).transact()


def main():
    events = set()
    last_seen_block = w3.eth.block_number
    while True:
        for event in round_free_contract.events.ModelScorers.create_filter(
            fromBlock=last_seen_block
        ).get_new_entries():
            if event not in events:
                events.add(event)
                last_seen_block = event["blockNumber"]
                if w3.eth.default_account in event["args"]["scorers"]:
                    asyncio.run(score_model(event["args"]["model"]))
        time.sleep(1)
