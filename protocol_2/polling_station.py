import sys
from asyncio import StreamReader, StreamWriter
from pathlib import Path

from simulaqron.settings import simulaqron_settings, network_config
from simulaqron.settings.network_config import NodeConfigType
from simulaqron.general.host_config import SocketsConfig
from simulaqron.sdk.protocol import SimulaQronClassicalClient

async def run_alice_protocol(reader: StreamReader, writer: StreamWriter) -> None:
    """
    Alice acts as the local polling terminal. She collects the classical
    intentions of the 4 citizens and submits them to Bob's quantum register.
    """
    # Test election: 4 Citizens. 3 vote Blue (0), 1 votes Red (1).
    citizen_votes = [0, 0, 0, 1]
    votes_str = ",".join(map(str, citizen_votes))
    
    payload = f"BALLOTS|{votes_str}\n"
    print(f"Alice: Submitting citizen ballot payload: {citizen_votes} (3x Blue, 1x Red)...", flush=True)
    writer.write(payload.encode())

    # Wait for the tallyman to finish the post-selection loop
    data = await reader.readline()
    reply = data.decode().strip()
    
    if reply.startswith("RESULT|"):
        _, scores = reply.split("|")
        blue_score, red_score = scores.split(",")
        print("\n" + "-"*45)
        print(f"Alice: Verified Official Election Outcome:")
        print(f"       Candidate BLUE : {float(blue_score):.2f} votes")
        print(f"       Candidate RED  : {float(red_score):.2f} votes")
        print("-"*45 + "\n")
    else:
        print(f"Alice: Received unexpected response from Tallyman: {reply}")

def make_run_alice():
    async def run_alice(reader: StreamReader, writer: StreamWriter) -> None:
        await run_alice_protocol(reader, writer)
        print("Alice: Session closed.")
    return run_alice

if __name__ == "__main__":
    _here = Path(__file__).parent
    simulaqron_settings.read_from_file(_here / "simulaqron_settings.json")
    network_config.read_from_file(_here / "simulaqron_network.json")
    
    sockets_config = SocketsConfig(network_config, "default", NodeConfigType.APP)
    client = SimulaQronClassicalClient(sockets_config)
    
    print("Alice: Connecting to Bob's Quantum Tallying Facility...")
    client.run_client("Bob", make_run_alice())