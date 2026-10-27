"""ASGI entrypoint: `uvicorn authpilot.main:app`."""

import logging

from authpilot.api import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

app = create_app()
