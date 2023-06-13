import aioipfs
import torch
from torch import nn


async def save_model_ipfs(state_dict: nn.Module.state_dict, ipfs_host: str) -> str:
    client = aioipfs.AsyncIPFS(maddr="/ip4/127.0.0.1/tcp/5001")
    torch.save(state_dict, "temp.pt")
    client.add("temp.pt")
    cids = [entry["Hash"] async for entry in client.add("temp.pt")]
    await client.close()
    return cids[0]


async def load_model_ipfs(cid: str, ipfs_host: str) -> nn.Module.state_dict:
    client = aioipfs.AsyncIPFS(maddr=ipfs_host)
    await client.get(path=cid)
    await client.close()
    return torch.load("temp.pt")
