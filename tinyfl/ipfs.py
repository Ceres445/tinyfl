import aioipfs
from torch import nn


async def save_model_ipfs(model: nn.Module):
    model.sav
    pass


async def load_model_ipfs():
    pass


# async def get(cid: str):
#     print(client)

#     await client.get(cid, dstdir=".")
#     await client.close()


# async def add_files(files: list):
#     client = aioipfs.AsyncIPFS(maddr="/ip4/127.0.0.1/tcp/5001")

#     async for added_file in client.add(*files, recursive=True):
#         print(
#             "Imported file {0}, CID: {1}".format(added_file["Name"], added_file["Hash"])
#         )

#     await client.close()
