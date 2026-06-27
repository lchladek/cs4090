# Quantum voting protocol test using Qiskit and AerSimulator.

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit_aer import AerSimulator


def build_voting_circuit(voter_votes):
    """Build the 5-qubit voting circuit."""

    q_id = QuantumRegister(2, "id")
    q_cand = QuantumRegister(2, "cand")
    q_anc = QuantumRegister(1, "ancilla")

    c_anc = ClassicalRegister(1, "c_anc")
    c_cand = ClassicalRegister(2, "c_cand")

    qc = QuantumCircuit(q_id, q_cand, q_anc, c_anc, c_cand)

    # Initialize registers
    qc.h(q_id)
    qc.h(q_cand[0])
    qc.cx(q_cand[0], q_cand[1])
    qc.h(q_anc[0])
    qc.barrier()

    # Voting
    qc.x(q_anc[0])

    for voter_id, vote in voter_votes.items():
        if voter_id[0] == "0":
            qc.x(q_id[0])
        if voter_id[1] == "0":
            qc.x(q_id[1])

        if vote == 0:
            qc.x(q_cand[0])
            qc.mcp(np.pi, [q_id[0], q_id[1], q_anc[0]], q_cand[0])
            qc.x(q_cand[0])
        else:
            qc.mcp(np.pi, [q_id[0], q_id[1], q_anc[0]], q_cand[0])

        if voter_id[0] == "0":
            qc.x(q_id[0])
        if voter_id[1] == "0":
            qc.x(q_id[1])

        qc.barrier()

    qc.x(q_anc[0])
    qc.h(q_anc[0])

    # Measure
    qc.measure(q_anc[0], c_anc[0])
    qc.measure(q_cand[0], c_cand[0])
    qc.measure(q_cand[1], c_cand[1])

    return qc


def run_election_simulation(votes, total_kiezers=4, vereiste_geldige_shots=100):
    """Run the voting simulation with post-selection."""

    print(f"\n--- ELECTION SIMULATION ({vereiste_geldige_shots} shots) ---")
    print(f"Votes: {votes}\n")

    qc = build_voting_circuit(votes)
    simulator = AerSimulator()
    compiled_circuit = transpile(qc, simulator)

    geldige_kandidaat_metingen = []
    totaal_pogingen = 0

    while len(geldige_kandidaat_metingen) < vereiste_geldige_shots:
        totaal_pogingen += 1
        result = simulator.run(compiled_circuit, shots=1).result()
        counts = result.get_counts()
        measured_bits = next(iter(counts))

        cand_str, anc_str = measured_bits.split(" ")

        if anc_str == "1":
            geldige_kandidaat_metingen.append(cand_str)

    blauw_count = geldige_kandidaat_metingen.count("00")
    rood_count = geldige_kandidaat_metingen.count("11")

    p_blauw = blauw_count / vereiste_geldige_shots
    p_rood = rood_count / vereiste_geldige_shots

    berekende_stemmen_blauw = p_blauw * total_kiezers
    berekende_stemmen_rood = p_rood * total_kiezers

    print(f"Total circuit executions: {totaal_pogingen}")
    print(f"Discarded runs:           {totaal_pogingen - vereiste_geldige_shots}")
    print(
        f"Post-selection success:   {(vereiste_geldige_shots / totaal_pogingen) * 100:.1f}% (Expected: 50.0%)"
    )

    print("\n--- MEASURED DISTRIBUTION ---")
    print(f"Blue (|00>): {p_blauw * 100:.1f}%")
    print(f"Red  (|11>): {p_rood * 100:.1f}%")

    print("\n--- FINAL RESULT ---")
    print(
        f"Blue votes: {berekende_stemmen_blauw:.2f} -> Rounded: {round(berekende_stemmen_blauw)}"
    )
    print(
        f"Red votes:  {berekende_stemmen_rood:.2f} -> Rounded: {round(berekende_stemmen_rood)}"
    )


if __name__ == "__main__":
    # Test: 3 Blue, 1 Red
    test_stemmen = {"00": 0, "01": 0, "10": 0, "11": 1}
    run_election_simulation(test_stemmen, total_kiezers=4, vereiste_geldige_shots=100)