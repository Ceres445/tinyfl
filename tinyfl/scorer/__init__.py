import json
import logging
from operator import itemgetter
import os
import sys
import time

from web3 import Web3

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
        model,
        scorer,
        endpoint,
        registration_contract_address,
        round_free_contract_address,
        ipfs_host,
    ) = itemgetter(
        "model",
        "scorer",
        "endpoint",
        "registration_contract_address",
        "round_free_contract_address",
        "ipfs_host",
    )(
        config
    )
    model = models[model]
    scorer = scorers[scorer]

logger.info(f"Model: {model.__name__}")
logger.info(f"Scorer: {scorer.__name__}")

w3 = Web3(Web3.HTTPProvider(endpoint))

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


def main():
    events = set()
    while True:
        for event in round_free_contract.events.ModelScorers.create_filter(
            fromBlock="latest"
        ).get_new_entries():
            if event not in events:
                events.add(event)
                print(event)
        time.sleep(1)
