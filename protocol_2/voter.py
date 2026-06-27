# UNUSED

import sys
from asyncio import StreamReader, StreamWriter
from pathlib import Path

from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalClient
from simulaqron.settings import network_config, simulaqron_settings
from simulaqron.settings.network_config import NodeConfigType

from netqasm.runtime.settings import set_simulator
set_simulator("simulaqron")
from netqasm.sdk.external import NetQASMConnection
from netqasm.sdk import EPRSocket


def cast_quantum_ballot(voter_id: str, choice: int) -> int:
    """Receives EPR half, encodes phase-flip (Red=Z), measures in X-basis."""
    epr_socket = EPRSocket("TallyCenter")
    sim_conn = NetQASMConnection(voter_id, epr_sockets=[epr_socket])

    q = epr_socket.recv_keep()[0]

    if choice == 1:
        q.Z()  # Encode Red via relative phase flip: |Phi+> -> |Phi->

    q.H()
    m = q.measure()

    sim_conn.flush()
    outcome = int(m)
    sim_conn.close()
    return outcome


def make_voter(voter_id: str, choice: int):
    async def run(reader: StreamReader, writer: StreamWriter):
        writer.write(f"{voter_id}\n".encode())
        await writer.drain()

        print(f"[{voter_id}] Connected. Standing by for EPR handshake...")
        signal = await reader.readline()
        if signal != b"GO\n":
            return

        print(f"[{voter_id}] Entangling... Encoding choice {'BLUE' if choice==0 else 'RED'}.")
        m_voter = cast_quantum_ballot(voter_id, choice)

        # Transmit classical share of the Bell measurement
        writer.write(f"{m_voter}\n".encode())
        await writer.drain()

        print(f"[{voter_id}] Quantum ballot cast. Waiting for global tally...")
        reply = (await reader.readline()).decode().strip()

        if reply.startswith("RESULT|"):
            _, data = reply.split("|")
            blue, red = data.split(",")
            print("\n" + "="*40)
            print(f"[{voter_id}] OFFICIAL ELECTION BROADCAST")
            print(f"           Blue Candidate : {float(blue)*100:.1f}%")
            print(f"           Red Candidate  : {float(red)*100:.1f}%")
            print("="*40 + "\n")

    return run


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python voter.py <VOTER_ID> <CHOICE_INT>")
        print("Example: python voter.py Voter_1 0  (0=Blue, 1=Red)")
        sys.exit(1)

    v_id = sys.argv[1]
    v_choice = int(sys.argv[2])

    _here = Path(__file__).parent
    simulaqron_settings.read_from_file(_here / "simulaqron_settings.json")
    network_config.read_from_file(_here / "simulaqron_network.json")

    cfg = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(cfg)

    client.run_client("TallyCenter", make_voter(v_id, v_choice))