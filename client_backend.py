import asyncio
import json
import threading
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import Awaitable, Callable

ROOT = Path(__file__).parent
BALLOT_QUBITS = 4
MAX_QUBITS = 20
HANDSHAKE_TIMEOUT = 10.0

FETCHING_CANDIDATES = "FETCHING_CANDIDATES"
READY = "READY"
FETCH_FAILED = "FETCH_FAILED"

CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
SUBMITTING = "SUBMITTING"
DONE = "DONE"
FAILED = "FAILED"

StateCallback = Callable[[str], None]

_simulator_ready = False


def _init_simulator() -> None:
    global _simulator_ready
    if _simulator_ready:
        return
    from netqasm.runtime.settings import set_simulator

    set_simulator("simulaqron")
    _simulator_ready = True


def _netqasm():
    _init_simulator()
    from netqasm.sdk import EPRSocket, Qubit
    from netqasm.sdk.external import NetQASMConnection

    return EPRSocket, Qubit, NetQASMConnection


def _simulaqron():
    from simulaqron.general.host_config import SocketsConfig
    from simulaqron.sdk.protocol import SimulaQronClassicalClient
    from simulaqron.settings import network_config, simulaqron_settings
    from simulaqron.settings.network_config import NodeConfigType

    return (
        SocketsConfig,
        SimulaQronClassicalClient,
        network_config,
        simulaqron_settings,
        NodeConfigType,
    )


async def run_voter(
    party: str,
    vote_bits: str,
    reader: StreamReader,
    writer: StreamWriter,
    on_state: StateCallback,
) -> str:
    on_state(CONNECTING)
    await send_json(writer, {"type": "hello", "party": party})
    on_state(CONNECTED)

    msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
    if msg.get("type") == "error":
        on_state(FAILED)
        return msg.get("message", "counter error")
    if msg.get("type") != "submit":
        on_state(FAILED)
        return f"unexpected message: {msg}"

    on_state(SUBMITTING)
    EPRSocket, _, NetQASMConnection = _netqasm()
    epr_socket = EPRSocket("Counter")
    conn = NetQASMConnection(
        party, epr_sockets=[epr_socket], max_qubits=MAX_QUBITS
    )

    ballot = prepare_ballot(conn, vote_bits)
    ballot = randomize_ballot(conn, ballot)
    ballot = encode_vote(conn, ballot, vote_bits)
    conn.flush()

    epr_qubits = epr_socket.recv_keep(number=BALLOT_QUBITS)
    conn.flush()

    corrections = teleport_ballot(conn, ballot, epr_qubits)
    conn.close()

    await send_json(
        writer,
        {"type": "corrections", "party": party, "bits": list(corrections)},
    )

    msg = await recv_json(reader)
    if msg.get("type") == "error":
        on_state(FAILED)
        return msg.get("message", "counter error")
    if msg.get("type") != "result":
        on_state(FAILED)
        return f"unexpected message: {msg}"

    on_state(DONE)
    return json.dumps(msg)


async def run_fetch_candidates(
    party: str,
    reader: StreamReader,
    writer: StreamWriter,
    on_state: StateCallback,
) -> dict[str, str]:
    on_state(FETCHING_CANDIDATES)
    await send_json(writer, {"type": "get_candidates", "party": party})
    msg = await recv_json(reader, timeout=HANDSHAKE_TIMEOUT)
    if msg.get("type") == "error":
        raise RuntimeError(msg.get("message", "administrator error"))
    if msg.get("type") != "candidates":
        raise RuntimeError(f"unexpected message: {msg}")
    candidates = msg.get("candidates", {})
    if not candidates:
        raise RuntimeError("empty candidate list")
    on_state(READY)
    return candidates


def fetch_candidates(party: str, on_state: StateCallback) -> dict[str, str]:
    result: dict[str, str] = {}
    error: list[str] = []

    async def handler(reader: StreamReader, writer: StreamWriter) -> None:
        try:
            result.update(await run_fetch_candidates(party, reader, writer, on_state))
        except (json.JSONDecodeError, OSError, RuntimeError, TimeoutError) as exc:
            error.append(str(exc))

    load_simulaqron_config()
    (
        SocketsConfig,
        SimulaQronClassicalClient,
        network_config,
        _,
        NodeConfigType,
    ) = _simulaqron()
    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(sockets_config)
    try:
        _run_client(client, "Administrator", handler, HANDSHAKE_TIMEOUT)
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
    (
        SocketsConfig,
        SimulaQronClassicalClient,
        network_config,
        _,
        NodeConfigType,
    ) = _simulaqron()
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
        _run_client(client, "Counter", handler, HANDSHAKE_TIMEOUT)
    except TimeoutError as exc:
        on_state(FAILED)
        on_result(str(exc))


def _run_client(
    client,
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


async def send_json(writer: StreamWriter, payload: dict) -> None:
    writer.write(json.dumps(payload).encode())
    await writer.drain()


async def recv_json(reader: StreamReader, timeout: float | None = None) -> dict:
    try:
        if timeout is not None:
            data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        else:
            data = await reader.read(4096)
    except asyncio.TimeoutError as exc:
        raise TimeoutError("handshake timed out") from exc
    if not data:
        raise ConnectionError("connection closed")
    return json.loads(data.decode())


def vote_to_bits(candidate: str, candidates: dict[str, str]) -> str:
    if candidate not in candidates:
        raise ValueError(f"unknown candidate: {candidate}")
    return candidates[candidate]


def load_simulaqron_config() -> None:
    _init_simulator()
    _, _, network_config, simulaqron_settings, _ = _simulaqron()
    simulaqron_settings.read_from_file(ROOT / "simulaqron_settings.json")
    network_config.read_from_file(ROOT / "simulaqron_network.json")


def prepare_ballot(conn, bits: str) -> list:
    raise NotImplementedError


def randomize_ballot(conn, ballot: list) -> list:
    raise NotImplementedError


def encode_vote(conn, ballot: list, bits: str) -> list:
    raise NotImplementedError


def teleport_ballot(conn, ballot: list, epr_qubits: list) -> tuple[int, ...]:
    raise NotImplementedError
