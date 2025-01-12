from contextlib import asynccontextmanager
import copy
import pickle
import threading
from typing import Any, Mapping
from fastapi import BackgroundTasks, FastAPI, Request
from torch.utils.data import DataLoader
from torchvision import datasets, transforms
import sys
import json
from operator import itemgetter
import uvicorn
import asyncio
import httpx
import logging
import os
from web3 import Web3
import time

from tinyfl.model import (
    models,
    splits,
    strategies,
)
from tinyfl.message import DeRegister, Register, StartRound, SubmitWeights
from tinyfl.ipfs import save_model_ipfs, load_model_ipfs

batch_size = 64

host: str
port: int

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s:     %(message)s - %(asctime)s",
)
logger = logging.getLogger(__name__)

client_lock = threading.Lock()
clients = set()


with open(sys.argv[1]) as f:
    config = json.load(f)
    (
        host,
        port,
        consensus,
        timeout,
        epochs,
        strategy,
        endpoint,
        registration_contract_address,
        round_free_contract_address,
        ipfs_host,
        cur_model,
        split,
    ) = itemgetter(
        "host",
        "port",
        "consensus",
        "timeout",
        "epochs",
        "strategy",
        "endpoint",
        "registration_contract_address",
        "round_free_contract_address",
        "ipfs_host",
        "model",
        "split",
    )(
        config
    )
    if strategies.get(strategy) is None:
        raise ValueError("Invalid aggregation model")
    aggregator = strategy
    strategy = strategies[strategy]
    split_dataset = splits[split]

logger.info(f"{host}:{port} loaded from config.")
logger.info(f"Consensus: {consensus}")
logger.info(f"Timeout: {timeout}")
logger.info(f"Epochs: {epochs}")
logger.info(f"Aggregation model: {strategy.__name__}")

msg_id = 0

round_lock = threading.Lock()
round_id = 0

model_lock = threading.Lock()
model = models[cur_model].create_model()
trainset, testset = model.create_datasets()
trainloader = DataLoader(trainset, batch_size=batch_size)
testloader = DataLoader(testset, batch_size=batch_size)

me = f"http://{host}:{port}"


quorum = threading.Condition()

clients_models_lock = threading.Lock()
client_models = dict()


def next_msg_id() -> int:
    global msg_id
    ack_id = msg_id
    msg_id += 1
    return ack_id


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


def run_rounds():
    time.sleep(30)
    while True:
        logger.info(f"Round {round_id} started")
        state_manager()
        logger.info(f"Round {round_id-1} ended")

        time.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    registration_contract.functions.registerDevice("trainer").call()
    worker = threading.Thread(target=run_rounds, daemon=True)
    worker.start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def ping():
    return {
        "success": True,
        "message": "pong!",
        "me": me,
        "round": round_id,
        "clients": clients,
    }


@app.post("/")
async def handle(req: Request, background_tasks: BackgroundTasks):
    msg = pickle.loads(await req.body())
    match msg:
        case Register(url=url):
            with client_lock:
                clients.add(url)
            logger.info(f"Client {url} registered")
            return {"success": True, "message": "Registered"}
        case SubmitWeights(round=round, weights=weights, url=url, final=final):
            background_tasks.add_task(
                collect_weights, url, copy.deepcopy(weights), final
            )
            return {"success": True, "message": "Weights submitted"}
        case DeRegister(url=url, id=id):
            with client_lock:
                clients.remove(url)
            logger.info(f"Client {url} de-registered")
            return {"success": True, "message": "De-registered"}
        case _:
            return {"success": False, "message": "Unknown message"}


def state_manager():
    global client_models
    with clients_models_lock:
        client_models = dict()
    for client in clients:
        client_models[client] = None
    asyncio.run(start_training())
    quorum_achieved: bool
    with quorum:
        logger.info("Waiting for quorum")
        quorum_achieved = quorum.wait(timeout)

        if not quorum_achieved and aggregator != "fedprox":
            logger.error("Quorum not achieved!")
            return
        else:
            logger.info("Quorum achieved!")
            # TODO: stop training after aggregation
            # asyncio.run(stop_training())
            with clients_models_lock:
                model.load_state_dict(
                    strategy(list(filter(lambda x: x != None, client_models.values())))
                )
            logger.info("Aggregated model")
            accuracy, loss = model.test_model(testloader)
            logger.info(f"Accuracy: {(accuracy):>0.1f}%, Loss: {loss:>8f}")
            logger.info("Submitting model to blockchain")
            cid = asyncio.run(save_model_ipfs(model.state_dict(), ipfs_host))
            logger.info(f"Model saved to IPFS with CID: {cid}")
            round_free_contract.functions.submitModel(cid).transact()
            logger.info(f"Model submitted to contarct")


#
# async def stop_training():
#     async with httpx.AsyncClient() as client:
#         return await asyncio.gather(
#             *[
#                 client.post(party, data=pickle.dumps(StopRound(msg_id=next_msg_id())))
#                 for party in clients
#             ]
#         )


async def start_training():
    global round_id
    round_id += 1

    curr_weights = copy.deepcopy(model.state_dict())
    client_indices = split_dataset(trainset, len(clients))

    async with httpx.AsyncClient() as client:
        return await asyncio.gather(
            *[
                client.post(
                    party,
                    data=pickle.dumps(
                        StartRound(
                            msg_id=next_msg_id(),
                            round=round_id,
                            epochs=epochs,
                            weights=curr_weights,
                            indices=indices,
                            aggregator=aggregator,
                        )
                    ),
                )
                for party, indices in zip(clients, client_indices)
            ]
        )


async def collect_weights(url: str, weights: Mapping[str, Any], final: bool):
    with round_lock:
        with quorum:
            with clients_models_lock:
                notify_quorum = False
                if aggregator == "fedavg":
                    models_submitted = len(
                        list(filter(lambda x: x != None, client_models.values()))
                    )
                    if models_submitted < consensus:
                        client_models[url] = weights
                        logger.info("Appended weights")
                        notify_quorum = (models_submitted + 1) == consensus
                    if notify_quorum:
                        quorum.notify()
                elif aggregator == "fedprox":
                    client_models[url] = weights
                    logger.info("Appended weights")


def main():
    uvicorn.run(app, port=int(port), host=host)


# from contextlib import asynccontextmanager
# import copy
# import pickle
# import threading
# from typing import Any, Mapping
# from fastapi import BackgroundTasks, FastAPI, Request
# from torch.utils.data import DataLoader
# from torchvision import datasets, transforms
# import sys
# import json
# from operator import itemgetter
# import uvicorn
# import asyncio
# import httpx
# import logging
# import os
# from web3 import Web3

# from tinyfl.model import (
#     models,
#     splits,
#     strategies,
# )
# from tinyfl.message import DeRegister, Register, StartRound, SubmitWeights
# from tinyfl.ipfs import save_model_ipfs, load_model_ipfs

# batch_size = 64

# host: str
# port: int

# logging.basicConfig(
#     stream=sys.stdout,
#     level=logging.INFO,
#     format="%(levelname)s:     %(message)s - %(asctime)s",
# )
# logger = logging.getLogger(__name__)

# client_lock = threading.Lock()
# clients = set()


# with open(sys.argv[1]) as f:
#     config = json.load(f)
#     (
#         host,
#         port,
#         consensus,
#         timeout,
#         epochs,
#         strategy,
#         endpoint,
#         registration_contract_address,
#         round_control_contract_address,
#         score_contract_address,
#         submit_model_contract_address,
#         round_free_contract_address,
#         ipfs_host,
#         cur_model,
#         split,
#     ) = itemgetter(
#         "host",
#         "port",
#         "consensus",
#         "timeout",
#         "epochs",
#         "strategy",
#         "endpoint",
#         "registration_contract_address",
#         "round_control_contract_address",
#         "score_contract_address",
#         "submit_model_contract_address",
#         "round_free_contract_address",
#         "ipfs_host",
#         "model",
#         "split",
#     )(
#         config
#     )
#     if strategies.get(strategy) is None:
#         raise ValueError("Invalid aggregation model")
#     aggregator = strategy
#     strategy = strategies[strategy]
#     split_dataset = splits[split]

# logger.info(f"{host}:{port} loaded from config.")
# logger.info(f"Consensus: {consensus}")
# logger.info(f"Timeout: {timeout}")
# logger.info(f"Epochs: {epochs}")
# logger.info(f"Aggregation model: {strategy.__name__}")

# msg_id = 0

# round_lock = threading.Lock()
# round_id = 0

# model_lock = threading.Lock()
# model = models[cur_model].create_model()
# trainset, testset = model.create_datasets()
# trainloader = DataLoader(trainset, batch_size=batch_size)
# testloader = DataLoader(testset, batch_size=batch_size)

# me = f"http://{host}:{port}"


# quorum = threading.Condition()

# clients_models_lock = threading.Lock()
# client_models = dict()


# def next_msg_id() -> int:
#     global msg_id
#     ack_id = msg_id
#     msg_id += 1
#     return ack_id


# w3 = Web3(Web3.HTTPProvider(endpoint))

# registration_contract = w3.eth.contract(
#     address=Web3.to_checksum_address(registration_contract_address),
#     abi=json.load(
#         open(
#             str(os.path.join(os.path.dirname(__file__), "../../abi/Registration.json"))
#         )
#     ),
# )

# # round_control_contract = w3.eth.contract(
# #     address=Web3.to_checksum_address(round_control_contract_address),
# #     abi=json.load(
# #         open(
# #             str(
# #                 os.path.join(os.path.dirname(__file__), "abi/RoundControlContract.json")
# #             )
# #         )
# #     )["abi"],
# # )

# # score_contract = w3.eth.contract(
# #     address=Web3.to_checksum_address(score_contract_address),
# #     abi=json.load(
# #         open(str(os.path.join(os.path.dirname(__file__), "abi/ScoreContract.json")))
# #     )["abi"],
# # )

# # submit_model_contract = w3.eth.contract(
# #     address=Web3.to_checksum_address(submit_model_contract_address),
# #     abi=json.load(
# #         open(str(os.path.join(os.path.dirname(__file__), "abi/SubmitModel.json")))
# #     )["abi"],
# # )

# round_free_contract = w3.eth.contract(
#     address=Web3.to_checksum_address(round_free_contract_address),
#     abi=json.load(
#         open(str(os.path.join(os.path.dirname(__file__), "../../abi/RoundFree.json")))
#     ),
# )


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     registration_contract.functions.registerDevice("trainer").call()
#     yield


# app = FastAPI(lifespan=lifespan)


# @app.get("/")
# async def ping():
#     return {
#         "success": True,
#         "message": "pong!",
#         "me": me,
#         "round": round_id,
#         "clients": clients,
#     }


# @app.get("/load_model")
# async def load_model(cid: str):
#     mod = await load_model_ipfs(cid, ipfs_host)
#     model = models[cur_model].create_model()
#     model.load_state_dict(mod)
#     accuracy, loss = model.test_model(testloader)
#     return {
#         "success": True,
#         "message": "Model loaded",
#         "accuracy": accuracy,
#         "loss": loss,
#     }


# @app.get("/start_round")
# async def start_round(background_tasks: BackgroundTasks):
#     background_tasks.add_task(state_manager)
#     return {"success": True}


# @app.post("/")
# async def handle(req: Request, background_tasks: BackgroundTasks):
#     msg = pickle.loads(await req.body())
#     match msg:
#         case Register(url=url):
#             with client_lock:
#                 clients.add(url)
#             logger.info(f"Client {url} registered")
#             return {"success": True, "message": "Registered"}
#         case SubmitWeights(round=round, weights=weights, url=url, final=final):
#             background_tasks.add_task(
#                 collect_weights, url, copy.deepcopy(weights), final
#             )
#             return {"success": True, "message": "Weights submitted"}
#         case DeRegister(url=url, id=id):
#             with client_lock:
#                 clients.remove(url)
#             logger.info(f"Client {url} de-registered")
#             return {"success": True, "message": "De-registered"}
#         case _:
#             return {"success": False, "message": "Unknown message"}


# def state_manager():
#     global client_models
#     with clients_models_lock:
#         client_models = dict()
#     for client in clients:
#         client_models[client] = None
#     asyncio.run(start_training())
#     quorum_achieved: bool
#     with quorum:
#         logger.info("Waiting for quorum")
#         quorum_achieved = quorum.wait(timeout)

#         if not quorum_achieved and aggregator != "fedprox":
#             logger.error("Quorum not achieved!")
#             return
#         else:
#             logger.info("Quorum achieved!")
#             # TODO: stop training after aggregation
#             # asyncio.run(stop_training())
#             with clients_models_lock:
#                 model.load_state_dict(
#                     strategy(list(filter(lambda x: x != None, client_models.values())))
#                 )
#             logger.info("Aggregated model")
#             accuracy, loss = model.test_model(testloader)
#             logger.info(f"Accuracy: {(accuracy):>0.1f}%, Loss: {loss:>8f}")
#             logger.info("Submitting model to blockchain")
#             cid = asyncio.run(save_model_ipfs(model.state_dict(), ipfs_host))
#             logger.info(f"Model saved to IPFS with CID: {cid}")


# #
# # async def stop_training():
# #     async with httpx.AsyncClient() as client:
# #         return await asyncio.gather(
# #             *[
# #                 client.post(party, data=pickle.dumps(StopRound(msg_id=next_msg_id())))
# #                 for party in clients
# #             ]
# #         )


# async def start_training():
#     global round_id
#     round_id += 1

#     curr_weights = copy.deepcopy(model.state_dict())
#     client_indices = split_dataset(trainset, len(clients))

#     async with httpx.AsyncClient() as client:
#         return await asyncio.gather(
#             *[
#                 client.post(
#                     party,
#                     data=pickle.dumps(
#                         StartRound(
#                             msg_id=next_msg_id(),
#                             round=round_id,
#                             epochs=epochs,
#                             weights=curr_weights,
#                             indices=indices,
#                             aggregator=aggregator,
#                         )
#                     ),
#                 )
#                 for party, indices in zip(clients, client_indices)
#             ]
#         )


# async def collect_weights(url: str, weights: Mapping[str, Any], final: bool):
#     with round_lock:
#         with quorum:
#             with clients_models_lock:
#                 notify_quorum = False
#                 if aggregator == "fedavg":
#                     models_submitted = len(
#                         list(filter(lambda x: x != None, client_models.values()))
#                     )
#                     if models_submitted < consensus:
#                         client_models[url] = weights
#                         logger.info("Appended weights")
#                         notify_quorum = (models_submitted + 1) == consensus
#                     if notify_quorum:
#                         quorum.notify()
#                 elif aggregator == "fedprox":
#                     client_models[url] = weights
#                     logger.info("Appended weights")


# def main():
#     uvicorn.run(app, port=int(port), host=host)
