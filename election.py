# PIECE_BITS (n), VOTE_BITS (m). See README.md.

PIECE_BITS = 3
VOTE_BITS = 3

CANDIDATES = {
    "Candidate A": "000",
    "Candidate B": "111",
    "Candidate C": "101",
}

VALID_VOTES = set(CANDIDATES.values())

PARTIES = ["Voter1", "Voter2", "Voter3"]

for _code in CANDIDATES.values():
    if len(_code) != VOTE_BITS:
        raise ValueError(f"candidate code {_code!r} must be {VOTE_BITS} bits")


PIECE_QUBIT_COUNT = PIECE_BITS + 1
BALLOT_QUBITS = VOTE_BITS * PIECE_QUBIT_COUNT
MAX_CONNECTION_QUBITS = max(50, 2 * BALLOT_QUBITS)
