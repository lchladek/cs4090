# This file handles the SimulaQron quantum part of the protocol for the client (voter).
# It is called from client.py, which handles the GUI.

import json
import threading
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import Awaitable, Callable

from netqasm.runtime.settings import set_simulator

set_simulator("simulaqron")

from netqasm.sdk import EPRSocket
from netqasm.sdk.external import NetQASMConnection
from election import BALLOT_QUBITS, MAX_CONNECTION_QUBITS
from protocol_io import (
    BALLOT_ISSUE_TIMEOUT,
    HANDSHAKE_TIMEOUT,
    log_correction,
    recv_json,
    send_json,
)
from quantum import apply_corrections, encode_vote, randomize_ballot, teleport_qubit
from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalClient
from simulaqron.settings import network_config, simulaqron_settings
from simulaqron.settings.network_config import NodeConfigType

ROOT = Path(__file__).parent

WAIT_CANDIDATES = "WAIT_CANDIDATES"
RECEIVING_BALLOT = "RECEIVING_BALLOT"
WAIT_VOTE = "WAIT_VOTE"
FETCH_FAILED = "FETCH_FAILED"

CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
SUBMITTING = "SUBMITTING"
WAIT_RESULT = "WAIT_RESULT"
DONE = "DONE"
FAILED = "FAILED"

StateCallback = Callable[[str], None]

_ballots: dict[str, tuple] = {}


def _release_ballot(party: str) -> None:
    session = _ballots.pop(party, None)
    if session is not None:
        session[0].close()


# This function performs the initial request to the Administrator,
# retrieving the candidate list and the ballot.
# In a real-world setup, this is where the 'trust' step would take place,
# with the admin only issuing ballots to voters that have authenticated
# by classical, physical, or other means.
async def run_admin_session(
    party: str,
    reader: StreamReader,
    writer: StreamWriter,
    on_state: StateCallback,
) -> dict[str, str]:
    state = WAIT_CANDIDATES
    candidates: dict[str, str] = {}

    while state != WAIT_VOTE:
        if state == WAIT_CANDIDATES:
            on_state(WAIT_CANDIDATES)
            await send_json(writer, {"type": "get_candidates", "party": party})
            msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
            print(f"Voter [{state}]: received {msg.get('type')}", flush=True)
            if msg.get("type") == "error":
                raise RuntimeError(msg.get("message", "administrator error"))
            if msg.get("type") != "candidates":
                raise RuntimeError(f"unexpected message: {msg}")
            candidates = msg.get("candidates", {})
            if not candidates:
                raise RuntimeError("empty candidate list")
            on_state(RECEIVING_BALLOT)
            state = RECEIVING_BALLOT

        elif state == RECEIVING_BALLOT:
            admin_conn = None
            try:
                admin_conn, epr_admin, epr_counter = open_voter_conn(party)
                await send_json(writer, {"type": "ballot_ready"})
                ballot = []
                for i in range(BALLOT_QUBITS):
                    ballot.append(epr_admin.create_keep()[0])
                    admin_conn.flush()
                    msg = await recv_json(reader, timeout=BALLOT_ISSUE_TIMEOUT)
                    if msg.get("type") == "error":
                        raise RuntimeError(msg.get("message", "administrator error"))
                    if msg.get("type") != "ballot_issued":
                        raise RuntimeError(f"unexpected message: {msg}")
                    corrections = msg.get("corrections", [])
                    if len(corrections) != 1:
                        raise RuntimeError("invalid ballot corrections")
                    pair = tuple(corrections[0])
                    log_correction(
                        party, "received", "ballot_issued", i + 1, BALLOT_QUBITS, pair
                    )
                    apply_corrections([ballot[-1]], [pair])
                    admin_conn.flush()

                _ballots[party] = (admin_conn, epr_counter, ballot)
                admin_conn = None
                on_state(WAIT_VOTE)
                state = WAIT_VOTE
            finally:
                if admin_conn is not None:
                    admin_conn.close()

    return candidates


# Encodes the vote_bits in the ballot and sends the ballot to the Counter.
async def run_voter(
    party: str,
    vote_bits: str,
    reader: StreamReader,
    writer: StreamWriter,
    on_state: StateCallback,
) -> str:
    session = _ballots.pop(party, None)
    if session is None:
        on_state(FAILED)
        return "no issued ballot; connect to Administrator first"

    admin_conn, epr_counter, ballot = session
    state = CONNECTING

    while state != DONE:
        if state == CONNECTING:
            on_state(CONNECTING)
            await send_json(writer, {"type": "hello", "party": party})
            on_state(CONNECTED)
            state = CONNECTED

        elif state == CONNECTED:
            msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
            print(f"Voter [{state}]: received {msg.get('type')}", flush=True)
            if msg.get("type") == "error":
                admin_conn.close()
                on_state(FAILED)
                return msg.get("message", "counter error")
            if msg.get("type") != "submit":
                admin_conn.close()
                on_state(FAILED)
                return f"unexpected message: {msg}"
            on_state(SUBMITTING)
            state = SUBMITTING

        elif state == SUBMITTING:
            try:
                ballot = randomize_ballot(admin_conn, ballot)
                ballot = encode_vote(admin_conn, ballot, vote_bits)

                for i, qubit in enumerate(ballot):
                    epr_qubit = epr_counter.create_keep()[0]
                    admin_conn.flush()
                    correction = teleport_qubit(admin_conn, qubit, epr_qubit)
                    log_correction(
                        party, "sent", "corrections", i + 1, BALLOT_QUBITS, correction
                    )
                    await send_json(
                        writer,
                        {"type": "corrections", "corrections": [list(correction)]},
                    )

                on_state(WAIT_RESULT)
                state = WAIT_RESULT
            finally:
                admin_conn.close()

        elif state == WAIT_RESULT:
            while True:
                msg = await recv_json(reader)
                print(f"Voter [{state}]: received {msg.get('type')} {msg.get('status')}", flush=True)
                if msg.get("type") == "error":
                    on_state(FAILED)
                    return msg.get("message", "counter error")
                if msg.get("type") != "result":
                    on_state(FAILED)
                    return f"unexpected message: {msg}"
                if msg.get("status") == "pending":
                    continue
                on_state(DONE)
                return json.dumps(msg)

    on_state(FAILED)
    return "unexpected end of voter session"


def open_voter_conn(party: str):
    epr_admin = EPRSocket("Administrator")
    epr_counter = EPRSocket("Counter")
    conn = NetQASMConnection(
        party,
        epr_sockets=[epr_admin, epr_counter],
        max_qubits=MAX_CONNECTION_QUBITS,
    )
    return conn, epr_admin, epr_counter


def load_simulaqron_config() -> None:
    simulaqron_settings.read_from_file(ROOT / "simulaqron_settings.json")
    network_config.read_from_file(ROOT / "simulaqron_network.json")


def run_client(
    client: SimulaQronClassicalClient,
    node: str,
    handler: Callable[[StreamReader, StreamWriter], Awaitable[None]],
    connect_timeout: float,
) -> None:
    connected = threading.Event()
    errors: list[Exception] = []

    async def wrapper(reader: StreamReader, writer: StreamWriter) -> None:
        connected.set()
        try:
            await handler(reader, writer)
        except Exception as exc:
            errors.append(exc)

    def target() -> None:
        try:
            client.run_client(node, wrapper)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    if not connected.wait(connect_timeout):
        raise TimeoutError(f"timed out connecting to {node}")
    thread.join()
    if errors:
        raise errors[0]


def fetch_candidates(party: str, on_state: StateCallback) -> dict[str, str]:
    _release_ballot(party)
    result: dict[str, str] = {}
    error: list[str] = []

    async def handler(reader: StreamReader, writer: StreamWriter) -> None:
        try:
            result.update(await run_admin_session(party, reader, writer, on_state))
        except (json.JSONDecodeError, OSError, RuntimeError, TimeoutError) as exc:
            error.append(str(exc))

    load_simulaqron_config()
    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(sockets_config)
    try:
        run_client(client, "Administrator", handler, BALLOT_ISSUE_TIMEOUT)
    except TimeoutError as exc:
        on_state(FETCH_FAILED)
        raise RuntimeError(str(exc)) from exc

    if error:
        on_state(FETCH_FAILED)
        raise RuntimeError(error[0])
    return result


def start_voter_client(
    party: str,
    vote_bits: str,
    on_state: StateCallback,
    on_result: Callable[[str], None],
) -> None:
    load_simulaqron_config()
    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(sockets_config)

    async def handler(reader: StreamReader, writer: StreamWriter) -> None:
        try:
            result = await run_voter(party, vote_bits, reader, writer, on_state)
            on_result(result)
        except Exception as exc:
            on_state(FAILED)
            on_result(str(exc))

    try:
        run_client(client, "Counter", handler, HANDSHAKE_TIMEOUT)
    except TimeoutError as exc:
        on_state(FAILED)
        on_result(str(exc))


def vote_to_bits(candidate: str, candidates: dict[str, str]) -> str:
    if candidate not in candidates:
        raise ValueError(f"unknown candidate: {candidate}")
    return candidates[candidate]
