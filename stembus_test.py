import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit_aer import AerSimulator


def build_voting_circuit(voter_votes):
    """Bouwt het 5-qubit kwantumstembus circuit voor 4 stemmers en 2 kandidaten.

    voter_votes: dict met de stem per ID, bijv. {'00': 0, '01': 0, '10': 0,
    '11': 1} (0=Blauw, 1=Rood)
    """
    # 5 Qubits definiëren
    q_id = QuantumRegister(2, "id")  # Qubits 0, 1: Kiezers-ID (4 opties)
    q_cand = QuantumRegister(
        2, "cand"
    )  # Qubits 2, 3: Kandidaat (00=Blauw, 11=Rood)
    q_anc = QuantumRegister(1, "ancilla")  # Qubit 4: Ancilla voor post-selectie

    # Klassieke registers om de metingen in op te slaan
    c_anc = ClassicalRegister(1, "c_anc")
    c_cand = ClassicalRegister(2, "c_cand")

    qc = QuantumCircuit(q_id, q_cand, q_anc, c_anc, c_cand)

    # --- STAP 1: INITIALISATIE ---
    # 1. Kiezers in gelijke superpositie brengen (|00>, |01>, |10>, |11>)
    qc.h(q_id[0])
    qc.h(q_id[1])

    # 2. Kandidaten-register in Bell-staat brengen (|00> + |11>)
    qc.h(q_cand[0])
    qc.cx(q_cand[0], q_cand[1])

    # 3. Ancilla in |+> staat brengen (Hadamard basis)
    qc.h(q_anc[0])
    qc.barrier()

    # --- STAP 2: DE STEMMING (Controlled Phase-Flips Z_k) ---
    # De stem-operatie vuurt af als de Ancilla op |0> staat (anti-controlled)
    # We draaien de ancilla tijdelijk om met een X-gate zodat we standaard
    # controls kunnen gebruiken.
    qc.x(q_anc[0])

    for voter_id, vote in voter_votes.items():
        # Bepaal op welke ID-staat we moeten triggeren
        # Als een ID-bit '0' is, doen we een X-gate om hem te activeren op '1'
        if voter_id[0] == "0":
            qc.x(q_id[0])
        if voter_id[1] == "0":
            qc.x(q_id[1])

        # De multi-controlled Z-gate (CCCZ)
        # Bepaal de target: stem 0 (Blauw) triggert op kandidaat-staat |00>.
        # Omdat q_cand[0] en q_cand[1] verstrengeld zijn, hoeven we alleen
        # q_cand[0] te targeten!
        if vote == 0:  # Blauw (-|00> + |11>)
            qc.x(q_cand[0])  # Activeer op |0>
            qc.mcp(
                np.pi, [q_id[0], q_id[1], q_anc[0]], q_cand[0]
            )  # CCCZ (pi phase)
            qc.x(q_cand[0])
        else:  # Rood (|00> - |11>)
            # Trigger direct op |1> van q_cand[0]
            qc.mcp(np.pi, [q_id[0], q_id[1], q_anc[0]], q_cand[0])

        # ID-qubits weer terugzetten
        if voter_id[0] == "0":
            qc.x(q_id[0])
        if voter_id[1] == "0":
            qc.x(q_id[1])

        qc.barrier()

    # Ancilla weer terugdraaien
    qc.x(q_anc[0])

    # --- STAP 3: TALLYMAN SLUIT DE STEMBUS ---
    # Tweede Hadamard op de ancilla om de fase-verschillen om te zetten in
    # meetbare waarschijnlijkheden
    qc.h(q_anc[0])

    # --- STAP 4: METING ---
    qc.measure(q_anc[0], c_anc[0])
    qc.measure(q_cand[0], c_cand[0])
    qc.measure(q_cand[1], c_cand[1])

    return qc


def run_election_simulation(votes, total_kiezers=4, vereiste_geldige_shots=100):
    """Draait de SimulaQron-stijl sampling loop tot we genoeg geldige
    post-selectie metingen hebben.
    """
    print(f"\n--- STARTELECTIE SIMULATIE ({vereiste_geldige_shots} shots) ---")
    print(f"Ingestelde stemmen: {votes}\n")

    qc = build_voting_circuit(votes)
    simulator = AerSimulator()
    compiled_circuit = transpile(qc, simulator)

    geldige_kandidaat_metingen = []
    totaal_pogingen = 0

    # Blijf loops draaien tot we exact het gewenste aantal succesvolle
    # post-selecties hebben
    while len(geldige_kandidaat_metingen) < vereiste_geldige_shots:
        totaal_pogingen += 1
        result = simulator.run(compiled_circuit, shots=1).result()
        counts = result.get_counts()
        gemeten_bits = list(counts.keys())[0]  # Format is "c_cand c_anc", bijv. "00 1"

        cand_str, anc_str = gemeten_bits.split(" ")

        # POST-SELECTIE: We accepteren de stem alleen als de ancilla '1' is
        if anc_str == "1":
            geldige_kandidaat_metingen.append(cand_str)

    # --- STAP 5: KLASSIEK RECONSTRUEREN VAN DE UITSLAG (1+1=2) ---
    blauw_count = geldige_kandidaat_metingen.count("00")
    rood_count = geldige_kandidaat_metingen.count("11")

    p_blauw = blauw_count / vereiste_geldige_shots
    p_rood = rood_count / vereiste_geldige_shots

    berekende_stemmen_blauw = p_blauw * total_kiezers
    berekende_stemmen_rood = p_rood * total_kiezers

    print(f"Totaal circuit executies nodig: {totaal_pogingen}")
    print(f"Wegegooide (mislukte) runs:     {totaal_pogingen - vereiste_geldige_shots}")
    print(
        f"Post-selectie succesratio:      {(vereiste_geldige_shots / totaal_pogingen) * 100:.1f}% (Theorie: 50.0%)"
    )
    print("\n--- GEMETEN KANSVERDELING ---")
    print(f"Kans op Blauw (|00>): {p_blauw * 100:.1f}%")
    print(f"Kans op Rood  (|11>): {p_rood * 100:.1f}%")

    print("\n--- EINDRESULTAAT (Kans * N) ---")
    print(f"Stemmen Blauw: {berekende_stemmen_blauw:.2f} -> Afgerond: {round(berekende_stemmen_blauw)} stemmen")
    print(f"Stemmen Rood:  {berekende_stemmen_rood:.2f} -> Afgerond: {round(berekende_stemmen_rood)} stemmen")


if __name__ == "__main__":
    # Testcase: 3 stemmen Blauw (0), 1 stem Rood (1)
    test_stemmen = {"00": 0, "01": 0, "10": 0, "11": 1}
    run_election_simulation(test_stemmen, total_kiezers=4, vereiste_geldige_shots=100)