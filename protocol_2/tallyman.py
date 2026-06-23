import sys
from asyncio import StreamReader, StreamWriter
from functools import partial
from pathlib import Path
from types import SimpleNamespace
import numpy as np

# QuTech / NetQASM boilerplate imports
from netqasm.runtime.settings import set_simulator
set_simulator("simulaqron")
from netqasm.sdk.external import NetQASMConnection
from netqasm.sdk.qubit import Qubit

from simulaqron.settings import simulaqron_settings, network_config
from simulaqron.settings.network_config import NodeConfigType
from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalServer

STATE_WAITING_BALLOTS = "WAITING_BALLOTS"
STATE_DONE = "DONE"

async def handle_ballots_bob(ctx: SimpleNamespace, writer: StreamWriter, raw_msg: str) -> str:
    """
    1. Parses the submitted citizen votes from Alice (e.g. "BALLOTS|0,0,0,1").
    2. Opens a NetQASM connection to act as the Centralized Quantum Tally Machine.
    3. Executes the Phase-Flip voting circuit for each vote independently until 
       we collect 'M' valid post-selected samples per citizen.
    4. Aggregates the probability distribution and scales by N (1+1=2 math).
    """
    try:
        header, votes_str = raw_msg.split("|", 1)
        votes = [int(v) for v in votes_str.split(",")]
    except (ValueError, AttributeError):
        print(f"Bob: Failed to parse ballot message '{raw_msg}'", flush=True)
        return STATE_WAITING_BALLOTS

    N = len(votes)
    samples_per_voter = 25  # We want 25 successful post-selections per voter (100 total)
    
    print(f"Bob: Received {N} citizen ballots. Starting quantum tallying engine...", flush=True)

    valid_candidate_measurements = []
    total_subroutines_dispatched = 0

    # Initialize the single-machine quantum tally center
    with NetQASMConnection("Bob") as conn:
        for voter_id, vote in enumerate(votes):
            voter_valid_samples = 0
            
            # Keep trying until this specific voter passes post-selection 25 times
            while voter_valid_samples < samples_per_voter:
                total_subroutines_dispatched += 1

                # Allocate the 3 required qubits for the core mechanism
                q_anc = Qubit(conn)
                q_candA = Qubit(conn)
                q_candB = Qubit(conn)

                # --- STEP 1: PREPARATION ---
                # Ancilla into |+>
                q_anc.H()
                # Candidate register into Bell state |00> + |11> (Blue vs Red)
                q_candA.H()
                q_candA.cnot(q_candB)

                # --- STEP 2: VOTING PHASE (Controlled Phase-Flips) ---
                # A vote is a discrete phase flip triggered when Ancilla is |0>.
                # We use the mathematical identity: trigger on |0> == wrap in X gates.
                
                if vote == 0:
                    # Vote BLUE: Phase flip on |0>_anc |00>_cand.
                    # Because candA and candB are entangled, targeting candA is enough!
                    q_anc.X()
                    q_candA.X()
                    q_anc.cphase(q_candA)  # cphase is a native Controlled-Z in NetQASM
                    q_candA.X()
                    q_anc.X()
                else:
                    # Vote RED: Phase flip on |0>_anc |11>_cand.
                    # Triggers when Ancilla is |0> AND candB is |1>.
                    q_anc.X()
                    q_anc.cphase(q_candB)
                    q_anc.X()

                # --- STEP 3: TALLYMAN INTERFERENCE ---
                # Transform phase differences back into observable population differences
                q_anc.H()

                # --- STEP 4: MEASUREMENT ---
                m_anc = q_anc.measure()
                m_candA = q_candA.measure()
                m_candB = q_candB.measure()

                # Dispatch instructions to the SimulaQron backend
                conn.flush()

                # --- STEP 5: POST-SELECTION ---
                # We only accept the universe branch where the ancilla collapsed to |1>
                if m_anc.value == 1:
                    voter_valid_samples += 1
                    cand_outcome = f"{m_candA.value}{m_candB.value}"
                    valid_candidate_measurements.append(cand_outcome)

    # --- STEP 6: CLASSICAL TALLY RECONSTRUCTION (1+1=2) ---
    total_valid = len(valid_candidate_measurements)
    blue_count = valid_candidate_measurements.count("00")
    red_count = valid_candidate_measurements.count("11")

    # Observed quantum probability mass
    p_blue = blue_count / total_valid
    p_red = red_count / total_valid

    # Scale probability by total citizens N to get exact linear votes
    calculated_blue_votes = p_blue * N
    calculated_red_votes = p_red * N

    print("\n" + "="*50, flush=True)
    print(f"Bob: Tallying complete!", flush=True)
    print(f"Bob: Total quantum runs dispatched : {total_subroutines_dispatched}", flush=True)
    print(f"Bob: Post-selection success rate   : {(total_valid / total_subroutines_dispatched)*100:.1f}% (Theory: 50.0%)", flush=True)
    print(f"Bob: Quantum Probability Mass      -> Blue: {p_blue*100:.1f}%, Red: {p_red*100:.1f}%", flush=True)
    print(f"Bob: Linear Vote Result (P * N)    -> BLUE: {calculated_blue_votes:.2f} (~{round(calculated_blue_votes)} votes)", flush=True)
    print(f"Bob: Linear Vote Result (P * N)    -> RED : {calculated_red_votes:.2f} (~{round(calculated_red_votes)} votes)", flush=True)
    print("="*50 + "\n", flush=True)

    # Transmit final tally back to Alice
    writer.write(f"RESULT|{calculated_blue_votes:.2f},{calculated_red_votes:.2f}\n".encode())
    return STATE_DONE


def make_run_bob():
    async def run_bob(reader: StreamReader, writer: StreamWriter) -> None:
        print("Bob: Polling station (Alice) connected.", flush=True)
        ctx = SimpleNamespace()
        state = STATE_WAITING_BALLOTS

        while state != STATE_DONE:
            data = await reader.readline()
            if not data:
                break
            raw_msg = data.decode().strip()
            if state == STATE_WAITING_BALLOTS:
                state = await handle_ballots_bob(ctx, writer, raw_msg)

        print("Bob: Classical server shutting down.")
    return run_bob


if __name__ == "__main__":
    _here = Path(__file__).parent
    simulaqron_settings.read_from_file(_here / "simulaqron_settings.json")
    network_config.read_from_file(_here / "simulaqron_network.json")
    
    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    server = SimulaQronClassicalServer(sockets_config, "Bob")
    server.register_client_handler(make_run_bob())
    
    print("Bob: Starting Centralized Quantum Tally Server...", flush=True)
    server.start_serving()