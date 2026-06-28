import sys
import random
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

from math import pi

CHSH_ROUNDS = 20


def make_polling_station(voter_id: str, choice: int):
    async def protocol(reader: StreamReader, writer: StreamWriter):
        epr_sock = EPRSocket("Tallyman")
        conn = NetQASMConnection(voter_id, epr_sockets=[epr_sock])

        writer.write(f"HELLO|{voter_id}\n".encode())
        await writer.drain()

        # =====================================================================
        # PHASE 1: CHSH ENTANGLEMENT VERIFICATION
        # =====================================================================
        for _ in range(CHSH_ROUNDS):
            chsh_data = (await reader.readline()).decode().strip()
            if not chsh_data:
                print(f"[{voter_id}] Empty CHSH message (server closed connection?)")
                conn.close()
                return

            parts = chsh_data.split("|")
            if len(parts) != 2 or parts[0] != "CHSH":
                print(f"[{voter_id}] Invalid CHSH message: {chsh_data!r}")
                conn.close()
                return

            basis_a = int(parts[1])

            q_chsh = epr_sock.recv_keep()[0]
            basis_b = random.choice([0, 1])

            # Bob settings (intended CHSH pair):
            # The angles are confusing, but these two are correct
            if basis_b == 0:
                q_chsh.rot_Y(angle=-pi/4)
            else:
                q_chsh.rot_Y(angle=pi/4)

            m_b = q_chsh.measure()
            conn.flush()
            # TEMP DEBUG:
            #print(f"[{voter_id}] CHSH tuple: x={basis_a}, y={basis_b}, b={int(m_b)}")

            writer.write(f"CHSH_RESP|{basis_b}|{int(m_b)}\n".encode())
            await writer.drain()

        # =====================================================================
        # PHASE 2: VOTING PHASE (MEMORY STORAGE)
        # =====================================================================
        phase_signal = await reader.readline()
        if phase_signal != b"VOTE_PHASE\n":
            print(f"[{voter_id}] Election aborted by Tallyman.")
            conn.close()
            return

        print(f"[{voter_id}] Entanglement healthy. Receiving Bell ballot...")
        q_ballot = epr_sock.recv_keep()[0]

        if choice == 1:
            q_ballot.Z()

        conn.flush()

        print(f"[{voter_id}] Ballot locked in quantum memory.")
        writer.write(b"VOTE_LOCKED\n")
        await writer.drain()

        # =====================================================================
        # PHASE 3: SEPARATED TALLY PHASE
        # =====================================================================
        print(f"[{voter_id}] Waiting for barrier release...")
        await reader.readline()

        print(f"[{voter_id}] Quorum met! Measuring in X-basis...")
        q_ballot.H()
        m_tally = q_ballot.measure()
        conn.flush()
        conn.close()

        writer.write(f"{voter_id}|{int(m_tally)}\n".encode())
        await writer.drain()

        broadcast = (await reader.readline()).decode().strip()
        _, scores = broadcast.split("|")
        p_blue, p_red = scores.split(",")

        print("\n" + "-"*45)
        print(f"[{voter_id}] OFFICIAL ELECTION BROADCAST")
        print(f"Blue Candidate : {float(p_blue)*100:.1f}%")
        print(f"Red Candidate  : {float(p_red)*100:.1f}%")
        print("-"*45 + "\n")

    return protocol


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python polling_station.py <Voter_ID> <Choice>")
        print("Example: python polling_station.py Voter_1 0")
        sys.exit(1)

    voter_id = sys.argv[1]
    choice = int(sys.argv[2])

    _here = Path(__file__).parent
    simulaqron_settings.read_from_file(_here / "simulaqron_settings.json")
    network_config.read_from_file(_here / "simulaqron_network.json")

    cfg = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(cfg)

    client.run_client("Tallyman", make_polling_station(voter_id, choice))