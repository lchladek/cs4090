# The Counter is the node that receives ballots from all voters.
# After the voters are received, the Administrator sends the secret measurement basis.
# The resulting measurements are validated and counted as votes.

import asyncio
import sys
import threading
from asyncio import StreamReader, StreamWriter
from pathlib import Path

from netqasm.runtime.settings import set_simulator

set_simulator("simulaqron")

from netqasm.sdk import EPRSocket
from netqasm.sdk.external import NetQASMConnection
from election import (
    CANDIDATES,
    PARTIES,
    BALLOT_QUBITS,
    MAX_CONNECTION_QUBITS,
    PIECE_QUBIT_COUNT,
)
from protocol_io import (
    BALLOT_ISSUE_TIMEOUT,
    HANDSHAKE_TIMEOUT,
    log_correction,
    recv_json,
    send_json,
)
from quantum import apply_corrections, decode_ballot_logged, measure_in_basis
from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalClient, SimulaQronClassicalServer
from simulaqron.settings import network_config, simulaqron_settings
from simulaqron.settings.network_config import NodeConfigType

ROOT = Path(__file__).parent

WAIT_HELLO = "WAIT_HELLO"
WAIT_SUBMIT = "WAIT_SUBMIT"
WAIT_FINAL = "WAIT_FINAL"
COUNTING = "COUNTING"
DONE = "DONE"

_counts: dict[str, int] = {name: 0 for name in CANDIDATES}
_pending: list[tuple[str, list]] = []
_secret_basis: list[int] | None = None
_ballot_lock = threading.Lock()
_counter_conn: NetQASMConnection | None = None
_counter_epr: dict[str, EPRSocket] = {}
_awaiting_final: dict[str, StreamWriter] = {}
_final_event: asyncio.Event | None = None


# Establishes or loads the NetQASM connection
def _ensure_counter_connection() -> tuple[NetQASMConnection, dict[str, EPRSocket]]:
    global _counter_conn, _counter_epr
    if _counter_conn is None:
        _counter_epr = {peer: EPRSocket(peer) for peer in PARTIES}
        _counter_conn = NetQASMConnection(
            "Counter",
            epr_sockets=list(_counter_epr.values()),
            max_qubits=max(MAX_CONNECTION_QUBITS, len(PARTIES) * BALLOT_QUBITS),
        )
    return _counter_conn, _counter_epr


def counter_connection() -> tuple[NetQASMConnection, dict[str, EPRSocket]]:
    with _ballot_lock:
        return _ensure_counter_connection()


def _reset_final_wait() -> None:
    global _final_event
    _awaiting_final.clear()
    _final_event = None


def _ensure_final_event() -> asyncio.Event:
    global _final_event
    if _final_event is None:
        _final_event = asyncio.Event()
    return _final_event


def _party_result(payload: dict, party: str) -> dict:
    mine = payload.get("votes", {}).get(party, {})
    return {
        "type": "result",
        "status": "accepted",
        "vote": mine.get("vote"),
        "candidate": mine.get("candidate"),
        "counts": payload.get("counts", {}),
        "votes": payload.get("votes", {}),
    }


# The main event loop for the counter
async def run_counter(reader: StreamReader, writer: StreamWriter) -> None:
    print("Counter: voter connected.", flush=True)
    state = WAIT_HELLO
    party = ""
    loop = asyncio.get_running_loop()

    while state != DONE:
        if state == WAIT_HELLO:
            try:
                msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
            except (TimeoutError, ConnectionError) as exc:
                print(f"Counter [{state}]: {exc}", flush=True)
                return

            print(f"Counter [{state}]: received {msg.get('type')}", flush=True)
            if msg.get("type") != "hello":
                await send_error(writer, "expected hello")
                return
            party = msg.get("party", "")
            if party not in PARTIES:
                await send_error(writer, "invalid party")
                return
            state = await handle_hello(writer)

        elif state == WAIT_SUBMIT:
            try:
                ballot_qubits_list = await receive_submitted_ballot(
                    reader, party, loop
                )
            except Exception as exc:
                await send_error(writer, str(exc))
                return
            state = await handle_buffered_ballot(writer, party, ballot_qubits_list)

        elif state == COUNTING:
            state = await handle_counting(writer, party)

        elif state == WAIT_FINAL:
            await _ensure_final_event().wait()
            state = DONE

    print(f"Counter: event loop finished (final state: {state}).", flush=True)


async def send_error(writer: StreamWriter, message: str) -> None:
    await send_json(writer, {"type": "error", "message": message})


async def handle_hello(writer: StreamWriter) -> str:
    if not _pending:
        _reset_final_wait()
    await send_json(writer, {"type": "submit"})
    return WAIT_SUBMIT


# Saves a voter's ballot to quantum memory. It is not measured immediately.
async def receive_submitted_ballot(
    reader: StreamReader, party: str, loop: asyncio.AbstractEventLoop
) -> list:
    ballot_qubits_list: list = []
    for i in range(BALLOT_QUBITS):
        ballot_qubits_list.append(await loop.run_in_executor(None, recv_one_epr, party))
        msg = await recv_json(reader, timeout=BALLOT_ISSUE_TIMEOUT)
        if msg.get("type") != "corrections":
            raise RuntimeError("expected corrections")
        corrections = msg.get("corrections", [])
        if len(corrections) != 1:
            raise RuntimeError("invalid corrections")
        pair = tuple(corrections[0])
        log_correction("Counter", "received", "corrections", i + 1, BALLOT_QUBITS, pair)
        conn, _ = counter_connection()
        apply_corrections([ballot_qubits_list[-1]], [pair])
        conn.flush()
    return ballot_qubits_list


# Responds to the client with a status message.
async def handle_buffered_ballot(
    writer: StreamWriter,
    party: str,
    ballot_qubits_list: list,
) -> str:
    _pending.append((party, ballot_qubits_list))
    ballots_received = len(_pending)
    print(f"Counter: buffered ballot from {party} ({ballots_received}/{len(PARTIES)})", flush=True)

    if ballots_received < len(PARTIES):
        _awaiting_final[party] = writer
        _ensure_final_event()
        await send_json(
            writer,
            {
                "type": "result",
                "status": "pending",
                "ballots_received": ballots_received,
                "ballots_needed": len(PARTIES),
            },
        )
        return WAIT_FINAL
    return COUNTING


async def handle_counting(writer: StreamWriter, party: str) -> str:
    try:
        last_vote, counts, votes = await count_pending()
    except Exception as exc:
        await send_error(writer, str(exc))
        return DONE

    payload = {
        "counts": counts,
        "votes": votes,
    }
    for waiting_party, waiting_writer in list(_awaiting_final.items()):
        try:
            await send_json(waiting_writer, _party_result(payload, waiting_party))
            print(f"Counter: sent final result to {waiting_party}", flush=True)
        except (ConnectionError, OSError) as exc:
            print(f"Counter: notify {waiting_party} failed: {exc}", flush=True)
    _awaiting_final.clear()
    event = _final_event
    if event is not None:
        event.set()

    await send_json(writer, _party_result(payload, party))
    return DONE


def recv_one_epr(peer: str):
    with _ballot_lock:
        conn, epr_sockets = _ensure_counter_connection()
        qubit = epr_sockets[peer].recv_keep()[0]
        conn.flush()
        return qubit


def _fetch_basis_blocking(ballots_received: int) -> list[int]:
    simulaqron_settings.read_from_file(ROOT / "simulaqron_settings.json")
    network_config.read_from_file(ROOT / "simulaqron_network.json")
    basis: list[int] = []
    error: list[str] = []

    async def handler(reader: StreamReader, writer: StreamWriter) -> None:
        await send_json(
            writer,
            {"type": "get_basis", "ballots_received": ballots_received},
        )
        msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
        if msg.get("type") == "error":
            error.append(msg.get("message", "administrator error"))
            return
        if msg.get("type") != "basis":
            error.append(f"unexpected message: {msg}")
            return
        bits = msg.get("bits", [])
        if len(bits) != PIECE_QUBIT_COUNT:
            error.append("invalid basis length")
            return
        basis.extend(int(b) for b in bits)

    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(sockets_config)
    client.run_client("Administrator", handler)

    if error:
        raise RuntimeError(error[0])
    if not basis:
        raise RuntimeError("empty basis from Administrator")
    return basis


# Get the secret basis from the Administrator. Measure all received ballots
# and if they are fully valid, adds them to the vote tally.
async def count_pending() -> tuple[str | None, dict[str, int], dict[str, dict]]:
    global _secret_basis, _pending, _counts, _counter_conn, _counter_epr

    if _secret_basis is None:
        loop = asyncio.get_running_loop()
        _secret_basis = await loop.run_in_executor(
            None, _fetch_basis_blocking, len(_pending)
        )
        print(f"Counter: measuring with basis {_secret_basis}", flush=True)

    conn, _ = counter_connection()
    last_vote: str | None = None
    counts = {name: 0 for name in CANDIDATES}
    votes: dict[str, dict] = {}
    for party, ballot_qubits_list in _pending:
        measured = measure_in_basis(ballot_qubits_list, _secret_basis, conn)
        vote_bits, valid = decode_ballot_logged(party, measured)
        entry = {
            "vote": vote_bits,
            "candidate": candidate_name(vote_bits) if valid and vote_bits else None,
            "accepted": bool(valid and vote_bits),
        }
        votes[party] = entry
        if valid and vote_bits:
            candidate = entry["candidate"]
            if candidate:
                counts[candidate] += 1
                last_vote = vote_bits
                print(
                    f"Counter: accepted {party} -> {candidate} ({vote_bits})",
                    flush=True,
                )
        else:
            print(f"Counter: rejected {party} vote={vote_bits}", flush=True)

    _counts = counts
    _pending.clear()
    conn.close()
    _counter_conn = None
    _counter_epr = {}
    _secret_basis = None
    print(f"Counter: counts {_counts}", flush=True)
    return last_vote, dict(_counts), votes


def candidate_name(vote_bits: str) -> str | None:
    for name, bits in CANDIDATES.items():
        if bits == vote_bits:
            return name
    return None


if __name__ == "__main__":
    simulaqron_settings.read_from_file(ROOT / "simulaqron_settings.json")
    network_config.read_from_file(ROOT / "simulaqron_network.json")

    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    server = SimulaQronClassicalServer(sockets_config, "Counter")
    server.register_client_handler(run_counter)
    print("Counter: starting server...", file=sys.stderr, flush=True)
    server.start_serving()
