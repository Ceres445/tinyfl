import aioipfs
import torch
from torch import nn


async def save_model_ipfs(state_dict: nn.Module.state_dict):
    client = aioipfs.AsyncIPFS(maddr="/ip4/127.0.0.1/tcp/5001")
    torch.save(state_dict, "temp.pt")
    client.add("temp.pt")
    await client.close()


async def load_model_ipfs(cid: str) -> nn.Module.state_dict:
    client = aioipfs.AsyncIPFS(maddr="/ip4/127.0.0.1/tcp/5001")
    await client.get(path=cid)
    await client.close()
    return torch.load("temp.pt")
