import asyncio
import logging

from .logger import console_print
from .server import start_server

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

def main() -> None:
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        console_print("Server stopped", "INFO")

if __name__ == "__main__":
    main()
