import re

from mitmproxy import http
from mitmproxy import flow
from ecies import encrypt, decrypt
from ecies.utils import generate_eth_key

secret_key, public_key = (eth_k := generate_eth_key(), (eth_k.to_hex(), eth_k.public_key.to_hex()))[-1]
print(f"Public key: {public_key}")

class WebSocketModifier:
    def websocket_message(self, flow: http.HTTPFlow):
        # Access the latest message
        message = flow.websocket.messages[-1]

        # Check if the message is from the client or server
        if message.from_client:
            if encrypted_ := re.search(b"\"input\\[type=password]\",\"value\":(\"[^\"]+\")", message.content):
                # print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                val = decrypt(secret_key, bytes.fromhex(encrypted_.group(1).decode().strip("\"")))
                message.content = re.sub(b"(\"input\\[type=password]\",\"value\"):(\"[^\"]+\")", b"\\1:\"" + val + b"\"", message.content)
                print(f"Modified client message: {message.content.decode()}")

        # You can also change the message type (TEXT or BINARY) if needed
        # message.is_text = False # for binary messages
        # message.is_text = True  # for text messages

addons = [
    WebSocketModifier()
]