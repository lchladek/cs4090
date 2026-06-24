import random
from functools import reduce
from operator import xor

from netqasm.sdk import Qubit

from election import PIECE_BITS, VOTE_BITS, VALID_VOTES, PIECE_QUBIT_COUNT, BALLOT_QUBITS


def _parity(bits: list[int]) -> int:
    return reduce(xor, bits, 0)


def _random_parity_bits(length: int) -> list[int]:
    head = [random.randint(0, 1) for _ in range(length)]
    head.append(_parity(head))
    return head


def encode_psi(qubit, a: int, b: int) -> None:
    if b == 1:
        qubit.H()
    if a == 1:
        qubit.X()


def prepare_blank_piece(conn, basis: list[int]) -> list:
    a = _random_parity_bits(PIECE_BITS)
    qubits = []
    for ak, bk in zip(a, basis):
        qubit = Qubit(conn)
        encode_psi(qubit, ak, bk)
        qubits.append(qubit)
    conn.flush()
    return qubits


def prepare_blank_ballot(conn, basis: list[int]) -> list:
    return list(iter_blank_ballot_qubits(conn, basis))


def iter_blank_ballot_qubits(conn, basis: list[int]):
    for _ in range(VOTE_BITS):
        a = _random_parity_bits(PIECE_BITS)
        for ak, bk in zip(a, basis):
            qubit = Qubit(conn)
            encode_psi(qubit, ak, bk)
            yield qubit
            conn.flush()


def ballot_piece(ballot: list, piece_index: int) -> list:
    width = PIECE_QUBIT_COUNT
    start = piece_index * width
    return ballot[start : start + width]


def randomize_piece(conn, piece: list) -> None:
    mask = _random_parity_bits(PIECE_BITS)
    for qubit, bit in zip(piece, mask):
        if bit:
            qubit.Y()
    conn.flush()


def randomize_ballot(conn, ballot: list) -> list:
    for piece_index in range(VOTE_BITS):
        randomize_piece(conn, ballot_piece(ballot, piece_index))
    return ballot


def encode_vote_piece(conn, piece: list, vote_bit: str) -> None:
    if vote_bit == "1":
        piece[PIECE_BITS].Y()
    conn.flush()


def encode_vote(conn, ballot: list, vote_bits: str) -> list:
    if len(vote_bits) != VOTE_BITS:
        raise ValueError(f"expected {VOTE_BITS} vote bits, got {len(vote_bits)}")
    for piece_index, bit in enumerate(vote_bits):
        encode_vote_piece(conn, ballot_piece(ballot, piece_index), bit)
    return ballot


def teleport_qubit(conn, qubit, epr_qubit) -> tuple[int, int]:
    qubit.cnot(epr_qubit)
    qubit.H()
    q_measure = qubit.measure()
    epr_measure = epr_qubit.measure()
    conn.flush()
    return int(q_measure), int(epr_measure)


def teleport_ballot(conn, ballot: list, epr_qubits: list) -> list[tuple[int, int]]:
    return [teleport_qubit(conn, qubit, epr_qubit) for qubit, epr_qubit in zip(ballot, epr_qubits)]


def apply_corrections(qubits: list, corrections: list[tuple[int, int]]) -> None:
    for qubit, (q_measure, epr_measure) in zip(qubits, corrections):
        if epr_measure == 1:
            qubit.X()
        if q_measure == 1:
            qubit.Z()


def measure_in_basis(qubits: list, basis: list[int], conn) -> list[int]:
    bits: list[int] = []
    width = PIECE_QUBIT_COUNT
    axis = [int(b) for b in basis[:width]]
    for piece_index in range(VOTE_BITS):
        piece = ballot_piece(qubits, piece_index)
        measurements = []
        for qubit, b in zip(piece, axis):
            if b == 1:
                qubit.H()
            measurements.append(qubit.measure())
        conn.flush()
        bits.extend(int(m) for m in measurements)
    return bits


def decode_piece(measurements: list[int]) -> tuple[int | None, int]:
    if len(measurements) != PIECE_QUBIT_COUNT:
        return None, 0
    bit = _parity(measurements)
    parity_ok = measurements[-1] == _parity(measurements[:-1])
    if bit == 0 and not parity_ok:
        return None, 0
    if bit == 1 and parity_ok:
        return None, 0
    return bit, 1


def decode_ballot(measurements: list[int]) -> tuple[str | None, bool]:
    if len(measurements) != BALLOT_QUBITS:
        return None, False

    vote_bits: list[str] = []
    width = PIECE_QUBIT_COUNT
    for piece_index in range(VOTE_BITS):
        start = piece_index * width
        piece_meas = measurements[start : start + width]
        bit, valid = decode_piece(piece_meas)
        if not valid or bit is None:
            return None, False
        vote_bits.append(str(bit))

    vote = "".join(vote_bits)
    return vote, vote in VALID_VOTES


def decode_ballot_logged(party: str, measurements: list[int]) -> tuple[str | None, bool]:
    print(f"Counter: {party} decode measured={measurements}", flush=True)
    if len(measurements) != BALLOT_QUBITS:
        print(
            f"Counter: {party} decode failed length {len(measurements)} != {BALLOT_QUBITS}",
            flush=True,
        )
        return None, False

    vote_bits: list[str] = []
    width = PIECE_QUBIT_COUNT
    for piece_index in range(VOTE_BITS):
        start = piece_index * width
        piece_meas = measurements[start : start + width]
        parity_ok = piece_meas[-1] == _parity(piece_meas[:-1])
        bit, valid = decode_piece(piece_meas)
        xor_all = _parity(piece_meas)
        print(
            f"Counter: {party} piece {piece_index + 1}/{VOTE_BITS} "
            f"meas={piece_meas} parity_ok={parity_ok} xor_all={xor_all} bit={bit}",
            flush=True,
        )
        if not valid or bit is None:
            return None, False
        vote_bits.append(str(bit))

    vote = "".join(vote_bits)
    ok = vote in VALID_VOTES
    print(f"Counter: {party} vote={vote} valid={ok}", flush=True)
    return vote, ok
