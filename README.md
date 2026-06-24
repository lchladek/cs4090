# Quantum Voting

[NTT conjugate-coding voting scheme](https://ntt-review.jp/archive/ntttechnical.php?contents=ntr200801sp3.html) (Okamoto, Suzuki, Tokunaga, 2008).

## Setup

```bash
pip install -r requirements.txt
```

## Configuration

`election.py`:

- `PIECE_BITS` (n): qubits per blank piece minus parity; basis `K` has `n + 1` bits
- `VOTE_BITS` (m): vote message length; ballot size is `m * (n + 1)` qubits
- `CANDIDATES`, `PARTIES`: valid vote codes and authorized voters

Raise `max_qubits` in `simulaqron_settings.json` if `MAX_CONNECTION_QUBITS` exceeds it.

## Run

```bash
simulaqron stop
simulaqron reset processes
simulaqron reset pidfiles

simulaqron start --nodes=Voter1,Voter2,Voter3,Administrator,Counter \
  --network-config-file simulaqron_network.json \
  --simulaqron-config-file simulaqron_settings.json
```

```bash
python administrator.py
python counter.py
python client.py --port 5000
python client.py --port 5001
python client.py --port 5002
```

Open one browser tab per client (e.g. http://127.0.0.1:5000). Message order is in [PROTOCOL.md](PROTOCOL.md).
