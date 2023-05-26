import sys
import asyncio

import aioipfs

async def add_files(files: list):
    client = aioipfs.AsyncIPFS(maddr='/ip4/127.0.0.1/tcp/5001')

    async for added_file in client.add(*files, recursive=True):
        print('Imported file {0}, CID: {1}'.format(
            added_file['Name'], added_file['Hash']))

    await client.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(add_files(sys.argv[1:]))
