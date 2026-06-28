# The Administrator picks a secret basis and issues ballots to the Voters
# The security of the protocol is that nobody that the Administrator didn't entrust with a ballot can vote.
# In a real deployment this would be authenticated (see my comment in client_backend.py)

import asyncio
import random
import sys
import threading
from asyncio import StreamReader, StreamWriter
from pathlib import Path

from netqasm.runtime.settings import set_simulator

set_simulator("simulaqron")

from netqasm.sdk import EPRSocket
from netqasm.sdk.external import NetQASMConnection
from election import CANDIDATES, PARTIES, PIECE_BITS, BALLOT_QUBITS, MAX_CONNECTION_QUBITS, PIECE_QUBIT_COUNT
from protocol_io import HANDSHAKE_TIMEOUT, log_correction, recv_json, send_json
from quantum import iter_blank_ballot_qubits, teleport_qubit
from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalServer
from simulaqron.settings import network_config, simulaqron_settings
from simulaqron.settings.network_config import NodeConfigType

ROOT = Path(__file__).parent

WAIT_MSG = "WAIT_MSG"
WAIT_BALLOT_READY = "WAIT_BALLOT_READY"
DONE = "DONE"

_secret_basis = [random.randint(0, 1) for _ in range(PIECE_QUBIT_COUNT)]
_ballots_issued: set[str] = set()
_ballot_lock = threading.Lock()


# The main event loop for the Administrator, which waits to send ballots to voters.
async def run_administrator(reader: StreamReader, writer: StreamWriter) -> None:
    state = WAIT_MSG
    party = ""

    while state != DONE:
        try:
            msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
        except (TimeoutError, ConnectionError) as exc:
            print(f"Administrator [{state}]: {exc}", flush=True)
            return

        print(f"Administrator [{state}]: received {msg.get('type')}", flush=True)

        if state == WAIT_MSG:
            msg_type = msg.get("type")
            if msg_type == "get_basis":
                state = await handle_get_basis(writer, msg)
                continue
            if msg_type != "get_candidates":
                await send_error(writer, "expected get_candidates")
                return
            state, party = await handle_get_candidates(writer, msg)
            if state == DONE:
                return

        elif state == WAIT_BALLOT_READY:
            if msg.get("type") != "ballot_ready":
                await send_error(writer, "expected ballot_ready")
                return
            state = await handle_ballot_ready(writer, party)

    print(f"Administrator: event loop finished (final state: {state}).", flush=True)


async def send_error(writer: StreamWriter, message: str) -> None:
    await send_json(writer, {"type": "error", "message": message})


async def handle_get_basis(writer: StreamWriter, msg: dict) -> str:
    ballots_received = msg.get("ballots_received", 0)
    if ballots_received < len(PARTIES):
        await send_error(
            writer,
            f"waiting for {len(PARTIES)} ballots, got {ballots_received}",
        )
        return DONE
    print("Administrator: released secret basis to Counter", flush=True)
    await send_json(writer, {"type": "basis", "bits": list(_secret_basis)})
    return DONE


async def handle_get_candidates(writer: StreamWriter, msg: dict) -> tuple[str, str]:
    party = msg.get("party", "")
    if party not in PARTIES:
        await send_error(writer, "unauthorized party")
        return DONE, ""
    if party in _ballots_issued:
        await send_error(writer, "ballot already issued")
        return DONE, ""

    await send_json(writer, {"type": "candidates", "candidates": CANDIDATES})
    return WAIT_BALLOT_READY, party


async def handle_ballot_ready(writer: StreamWriter, party: str) -> str:
    conn = None
    try:
        epr_socket = EPRSocket(party)
        with _ballot_lock:
            conn = NetQASMConnection(
                "Administrator",
                epr_sockets=[epr_socket],
                max_qubits=MAX_CONNECTION_QUBITS,
            )
        blank_qubits = iter_blank_ballot_qubits(conn, _secret_basis)
        for i in range(BALLOT_QUBITS):
            with _ballot_lock:
                epr_half = epr_socket.recv_keep()[0]
                conn.flush()
                blank_qubit = next(blank_qubits)
                correction = teleport_qubit(conn, blank_qubit, epr_half)
            log_correction(
                "Administrator", "sent", "ballot_issued", i + 1, BALLOT_QUBITS, correction
            )
            await send_json(
                writer,
                {"type": "ballot_issued", "corrections": [list(correction)]},
            )
    except Exception as exc:
        await send_error(writer, f"ballot issue failed: {exc}")
        return DONE
    finally:
        if conn is not None:
            conn.close()

    _ballots_issued.add(party)
    print(f"Administrator: issued blank ballot to {party}", flush=True)
    return DONE


if __name__ == "__main__":
    simulaqron_settings.read_from_file(ROOT / "simulaqron_settings.json")
    network_config.read_from_file(ROOT / "simulaqron_network.json")

    print(
        f"Administrator: n={PIECE_BITS}, ballot={BALLOT_QUBITS} qubits, basis unreleased",
        file=sys.stderr,
        flush=True,
    )

    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    server = SimulaQronClassicalServer(sockets_config, "Administrator")
    server.register_client_handler(run_administrator)
    print("Administrator: starting server...", file=sys.stderr, flush=True)
    server.start_serving()
