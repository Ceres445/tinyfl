import sys
import asyncio

import aioipfs

async def get(cid: str):
    client = aioipfs.AsyncIPFS(maddr='/ip4/127.0.0.1/tcp/5001')
    print(client)

    await client.get(cid, dstdir='.')
    await client.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(get(sys.argv[1]))
