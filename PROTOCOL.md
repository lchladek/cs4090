# Protocol

## Administrator (authenticated)

```
Voter to Administrator:
  {"type": "get_candidates", "party": "Voter1"}

Administrator to Voter:
  {"type": "candidates", "candidates": {"Candidate A": "000", "Candidate B": "111", "Candidate C": "101"}}

Voter creates EPR pairs with Administrator.

Voter to Administrator:
  {"type": "ballot_ready"}

Administrator teleports blank ballot to Voter one qubit at a time.

Administrator to Voter (once per qubit):
  {"type": "ballot_issued", "corrections": [[0, 1]]}
```

## Administrator to Counter

```
Counter to Administrator:
  {"type": "get_basis", "ballots_received": 3}

Administrator to Counter:
  {"type": "basis", "bits": [0, 1, 0, 0]}
```

`get_basis` succeeds only when `ballots_received` equals `len(PARTIES)`.

## Counter (anonymous)

```
Voter to Counter:
  {"type": "hello", "party": "Voter1"}

Counter to Voter:
  {"type": "submit"}

Voter creates EPR pairs with Counter and teleports ballot one qubit at a time.

Voter to Counter (once per qubit):
  {"type": "corrections", "corrections": [[0, 1]]}

Counter to Voter (ballots still outstanding):
  {"type": "result", "status": "pending", "ballots_received": 1, "ballots_needed": 3}

Counter to Voter (after counting):
  {"type": "result", "status": "accepted", "vote": "000""candidate": "Candidate A, ", "counts": {"Candidate A": 1, "Candidate B": 0, "Candidate C": 0}}
```

## Errors

Any party:

```
{"type": "error", "message": "..."}
```
