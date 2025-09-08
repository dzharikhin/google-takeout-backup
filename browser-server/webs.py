import asyncio
import json
import os
import re
import sys

import coincurve
import eth_keys
import websockets
from ecies import decrypt
from websockets import ServerConnection


def generate_keys():
    return (
        eth_k := eth_keys.keys.PrivateKey(coincurve.utils.get_valid_secret()),
        (eth_k.to_hex(), eth_k.public_key.to_hex()),
    )[-1]


secret_key = os.getenv("SK")
public_key = os.getenv("PK")
if not secret_key or not public_key:
    secret_key, public_key = generate_keys()
backend_uri = os.getenv("BROWSER_URL")
MAX_MESSAGE_SIZE = (2**30) * 4

not_shutdown = [1]


async def proxy_websocket(client_websocket: ServerConnection):
    try:
        async with websockets.connect(
            backend_uri, max_size=MAX_MESSAGE_SIZE
        ) as backend_websocket:
            print(f"connected to {backend_uri=}")

            async def client_to_backend():
                async for message in client_websocket:
                    # print(f"{message=}")
                    await backend_websocket.send(await process_message(message))

            async def backend_to_client():
                async for message in backend_websocket:
                    await client_websocket.send(message)

            await asyncio.gather(client_to_backend(), backend_to_client())

    except websockets.exceptions.ConnectionClosedOK:
        print("Backend connection closed normally.")


async def process_message(message):
    modified_message = message
    # print(f"[{isinstance(message, bytes)}]{message=}")
    if encrypted_ := re.search('"input\\[type=password]","value":("[^"]+")', message):
        try:
            val = decrypt(secret_key, bytes.fromhex(encrypted_.group(1).strip('"')))
            modified_message = re.sub(
                '("input\\[type=password]","value"):("[^"]+")',
                f'\\1:"{val.decode()}"',
                message,
            )
            # print(f"{modified_message=}")
        except ValueError:
            print(f"failed to parse encrypted {message=}")
    elif re.search('"method":"newContext",.+"storageState":', message):
        message_dict = json.loads(message)
        encoded_state = message_dict["params"]["storageState"]["encoded_value"]
        val = decrypt(secret_key, bytes.fromhex(encoded_state))
        message_dict["params"]["storageState"] = json.loads(val)
        modified_message = json.dumps(message_dict)
    return modified_message


async def main():
    port = int(os.getenv("PROXY_LISTEN_PORT", "8080"))

    async with websockets.serve(
        proxy_websocket, "0.0.0.0", port, max_size=MAX_MESSAGE_SIZE
    ) as server:
        print(f"started at {port=}")
        print(
            f"Encode pass with public key: https://dzharikhin.github.io/ecies/?pk={public_key}"
        )
        await server.serve_forever()


if __name__ == "__main__":
    if "--generate-only" in sys.argv:
        s_key, p_key = generate_keys()
        print(f"{s_key=},{p_key=}")
    else:
        asyncio.run(main())
