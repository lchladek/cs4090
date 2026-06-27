import asyncio
import random
from asyncio import StreamReader, StreamWriter
from pathlib import Path

from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalServer
from simulaqron.settings import network_config, simulaqron_settings
from simulaqron.settings.network_config import NodeConfigType

from netqasm.runtime.settings import set_simulator
set_simulator("simulaqron")
from netqasm.sdk.external import NetQASMConnection
from netqasm.sdk import EPRSocket
from netqasm.sdk.qubit import Qubit

EXPECTED_VOTERS = 3
VOTER_NODES = ["Voter_1", "Voter_2", "Voter_3"]
CHSH_ROUNDS = 4


class QuantumRAM:
    """Simulates persistent Quantum Memory across asynchronous Python lifecycles."""
    def __init__(self):
        self.epr_sockets = {name: EPRSocket(name) for name in VOTER_NODES}
        self.conn = NetQASMConnection("Tallyman", epr_sockets=list(self.epr_sockets.values()))
        self.stored_ballots: dict[str, Qubit] = {}
        self.active_sessions: dict[str, tuple[StreamReader, StreamWriter]] = {}
        self.hw_lock = asyncio.Lock()
        self.quorum_reached = asyncio.Event()

    def shutdown(self):
        self.conn.close()


async def verify_chsh_channel(qram: QuantumRAM, voter_id: str, reader: StreamReader, writer: StreamWriter) -> bool:
    """Executes 4 CHSH rounds; requires >= 3 wins to verify Bell state fidelity."""
    wins = 0
    for _ in range(CHSH_ROUNDS):
        basis_a = random.choice([0, 1])

        writer.write(f"CHSH|{basis_a}\n".encode())
        await writer.drain()

        q_chsh = qram.epr_sockets[voter_id].create_keep()[0]
        if basis_a == 1:
            q_chsh.H()
        m_a = q_chsh.measure()
        
        qram.conn.flush()

        line = await reader.readline()
        if not line: return False
        parts = line.decode().strip().split("|")
        basis_b, m_b = int(parts[1]), int(parts[2])

        if (int(m_a) ^ m_b) == (basis_a & basis_b):
            wins += 1

    print(f"Tallyman: [{voter_id}] CHSH Score -> {wins}/{CHSH_ROUNDS} (Required: >= 3)", flush=True)
    return wins >= 3


def make_voter_handler(qram: QuantumRAM):
    async def handler(reader: StreamReader, writer: StreamWriter):
        raw = await reader.readline()
        if not raw: return
        parts = raw.decode().strip().split("|")
        voter_id = parts[1] if len(parts) > 1 else parts[0]

        async with qram.hw_lock:
            print(f"Tallyman: [{voter_id}] checking Bell pair fidelity...", flush=True)
            passed_chsh = await verify_chsh_channel(qram, voter_id, reader, writer)
            
            if not passed_chsh:
                print(f"Tallyman: [{voter_id}] REJECTED. Entanglement degraded!", flush=True)
                writer.close()
                return

            writer.write(b"VOTE_PHASE\n")
            await writer.drain()

            q_ballot = qram.epr_sockets[voter_id].create_keep()[0]
            qram.stored_ballots[voter_id] = q_ballot
            qram.conn.flush()

            await reader.readline()
            qram.active_sessions[voter_id] = (reader, writer)

        print(f"Tallyman: [{voter_id}] ballot stored in Q-RAM. ({len(qram.stored_ballots)}/{EXPECTED_VOTERS})", flush=True)

        if len(qram.stored_ballots) == EXPECTED_VOTERS:
            print("\n" + "="*50, flush=True)
            print("Tallyman: All votes secured. CLOSING VOTING PHASE.", flush=True)
            print("Tallyman: MEASURING ALL STORED BELL PAIRS IN X-BASIS...", flush=True)
            print("="*50 + "\n", flush=True)

            async with qram.hw_lock:
                center_outcomes = {}
                for v_id, q in qram.stored_ballots.items():
                    q.H()
                    center_outcomes[v_id] = q.measure()
                qram.conn.flush()

                for _, w in qram.active_sessions.values():
                    w.write(b"START_TALLY\n")
                    await w.drain()

                final_votes = []
                for v_name, (r, _) in qram.active_sessions.items():
                    v_line = await r.readline()
                    v_bit = int(v_line.decode().strip().split("|")[1])
                    final_votes.append(int(center_outcomes[v_name]) ^ v_bit)

            total_valid = max(len(final_votes), 1)
            p_blue = final_votes.count(0) / total_valid
            p_red = final_votes.count(1) / total_valid
            broadcast_msg = f"RESULT|{p_blue:.2f},{p_red:.2f}\n".encode()

            for _, w in qram.active_sessions.values():
                w.write(broadcast_msg)
                await w.drain()
            qram.quorum_reached.set()

        await qram.quorum_reached.wait()
        writer.close()

    return handler


if __name__ == "__main__":
    _here = Path(__file__).parent
    simulaqron_settings.read_from_file(_here / "simulaqron_settings.json")
    network_config.read_from_file(_here / "simulaqron_network.json")

    qram = QuantumRAM()
    cfg = SocketsConfig(network_config, "default", NodeConfigType.APP)
    server = SimulaQronClassicalServer(cfg, "Tallyman")
    server.register_client_handler(make_voter_handler(qram))

    try:
        print(f"Tallyman: Server online ({EXPECTED_VOTERS} voters expected)...", flush=True)
        server.start_serving()
    finally:
        qram.shutdown()