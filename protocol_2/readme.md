# Quantum Voting Protocol using SimulaQron

## Overview

This protocol implements a distributed quantum voting protocol inspired by the paper by Aydin & Daskin [1] on Bell-state based quantum voting. It is the second protocol by our awesome company QuVote and is available for your high-security voting needs. The implementation demonstrates how quantum entanglement can be used to encode and anonymously tally votes using Bell pairs in a simulated quantum network.

The protocol is implemented using NetQASM and SimulaQron, where each voter is represented as an independent network node communicating with a central tally authority.

Because SimulaQron models a distributed quantum network rather than a single large quantum computer, some parts of the original protocol cannot be implemented directly. This project therefore focuses on the distributed Bell-pair voting mechanism while preserving the core quantum principles of the original proposal.

---

# Implemented Features

## Bell Pair Distribution

The tallyman creates an EPR pair for every voter using NetQASM's `create_keep()` operation.

Each voter receives the second half of the Bell pair using `recv_keep()`.

These Bell pairs are used as the quantum ballots.

---

## Quantum Vote Encoding

Votes are encoded through a local phase flip on the voter's qubit.

* **Blue candidate:** no operation
* **Red candidate:** apply a Pauli-Z gate

This transforms

$$
|\Phi^+\rangle \longrightarrow |\Phi^-\rangle
$$

without revealing the vote to the tallyman.

---

## Persistent Quantum Memory

A persistent `NetQASMConnection` is maintained by the tallyman, allowing Bell-pair references to remain alive throughout the election.

This simulates quantum memory and separates the voting phase from the tally phase.

---

## CHSH Entanglement Verification

Before voting starts, every communication channel is verified using a CHSH game.

For every voter:

* 20 Bell pairs are generated
* random measurement bases are selected
* the CHSH winning condition is evaluated
* the voter is accepted only if at least 75% of the rounds succeed

This verifies that sufficiently strong quantum correlations exist before the election proceeds.

---

## Separated Voting and Tally Phases

The protocol is divided into four distinct phases:

1. CHSH verification
2. Voting
3. Synchronization
4. Tally

All Bell pairs remain unmeasured until every voter has completed the voting phase.

Only after the election closes are all Bell pairs measured simultaneously in the X basis to determine the final tally.

---

## Distributed Architecture

Each participant executes as an independent SimulaQron node.

* Tallyman
* Voter 1
* Voter 2
* Voter 3

Classical communication is performed through SimulaQron's application sockets, while quantum communication uses NetQASM EPR sockets.

---

# Protocol Workflow

```
             +----------------------+
             |   Tallyman starts    |
             +----------+-----------+
                        |
                        v
           CHSH Verification (20 rounds)
                        |
             Channel verified?
                /            \
             No              Yes
             |                |
        Connection        Create Bell Pair
          closed               |
                               v
                    Voter encodes vote
                         (optional Z)
                               |
                               v
                    Bell pair stored
                               |
                    All voters finished?
                               |
                               v
                 Measure all Bell pairs
                    in the X basis
                               |
                               v
                    XOR classical outcomes
                               |
                               v
                    Broadcast election result
```

---

# Relation to the Original Paper

This implementation reproduces the distributed Bell-state voting mechanism presented in the paper but does not implement every component of the original protocol.

### Implemented

* Bell-pair distribution
* Local phase-flip vote encoding
* X-basis Bell-state decoding
* Distributed network architecture
* Entanglement verification using a CHSH game
* Separation between voting and tally phases

### Not Implemented

The following components from the original paper are not implemented because they require a single quantum computer rather than a distributed quantum network:

* Quantum identity register
* Candidate superposition register
* Ancilla-assisted Hadamard test
* Controlled-controlled-Z (CCZ) voting gates
* Ancilla post-selection
* Global quantum state spanning all voters simultaneously

These features cannot currently be realised within the distributed programming model provided by NetQASM and SimulaQron.

---

# Running the Protocol

Start the tallyman:

```bash
python tallyman.py
```

Run each voter in a separate terminal:

```bash
python polling_station.py Voter_1 0
python polling_station.py Voter_2 1
python polling_station.py Voter_3 0
```

where

* `0` = Blue candidate
* `1` = Red candidate

The scripts `initialize.sh` and `start_venv.sh` can be used to start simulaqron and activate the environment. (`initialize.sh` does both, `start_venv.sh` only activate the environment.)
```bash
source initialize.sh
source start_venv.sh
```

---

# Expected Output

Each voter first performs CHSH verification.

Example:

```
CHSH Score -> 18/20
```

If verification succeeds, voting begins.

After every voter has cast a ballot, the tallyman measures all stored Bell pairs simultaneously and broadcasts the election result:

```
Blue Candidate : 66.7%
Red Candidate  : 33.3%
```

---

## References

1. Aydin, A. E., & Daskin, A. (2026). *Quantum Voting Protocol for Centralized and Distributed Voting Based on Phase-Flip Counting*. arXiv:2510.15243. https://arxiv.org/abs/2510.15243