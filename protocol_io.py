# Some common functions for classical communication and logging

import asyncio
import json
from asyncio import StreamReader, StreamWriter

HANDSHAKE_TIMEOUT = 10.0
BALLOT_ISSUE_TIMEOUT = 120.0


def log_correction(
    party: str, verb: str, msg_type: str, index: int, total: int, correction
) -> None:
    q, z = correction
    print(f"{party}: {verb} {msg_type} {index}/{total} [{q}, {z}]", flush=True)


async def send_json(writer: StreamWriter, payload: dict) -> None:
    writer.write(json.dumps(payload).encode() + b"\n")
    await writer.drain()


async def recv_json(reader: StreamReader, timeout: float | None = None) -> dict:
    try:
        if timeout is not None:
            data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        else:
            data = await reader.readline()
    except asyncio.TimeoutError as exc:
        raise TimeoutError("handshake timed out") from exc
    if not data:
        raise ConnectionError("connection closed")
    return json.loads(data.decode())
