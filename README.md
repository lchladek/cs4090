# Quantum Voting

Skeleton for a conjugate-coding quantum voting app inspired by [Quantum Voting Scheme Based on Conjugate Coding](https://ntt-review.jp/archive/ntttechnical.php?contents=ntr200801sp3.html) by Okamoto, Suzuki, and Tokunaga (2008).

## Setup

```bash
pip install -r requirements.txt
```

## Run


1. Make sure SimulaQron has stopped cleanly:
```bash
simulaqron stop
```
and if not, run:
```bash
simulaqron reset processes
simulaqron reset pidfiles
```

2. Start SimulaQron:

```bash
simulaqron start
```

3. Launch the voter client:

```bash
python client.py
```

Open http://127.0.0.1:5000 in a browser. Select a party and click Connect.

See [PROTOCOL.md](PROTOCOL.md) for message formats. Administrator and Counter servers are not implemented yet.
