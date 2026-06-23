# Protocol

Voter -> Administrator:

```json
{"type": "get_candidates", "party": "Voter1"}
```

Administrator -> voter:

```json
{"type": "candidates", "candidates": {"Candidate A": "000", "Candidate B": "111"}}
```

EPR pairs generated between Administrator and Voter. Administrator teleports a blank ballot to the voter. (not implemented)

Voter applies random Y flips to the ballot. Voter encodes their candidate choice onto the ballot. (not implemented)

Voter -> Counter:

```json
{"type": "hello", "party": "Voter1"}
```

Counter -> voter:

```json
{"type": "submit"}
```

EPR pairs generated between Counter and Voter. Voter prepares the ballot, teleports it to Counter.

Voter -> Counter:

```json
{"type": "corrections", "party": "Voter1", "bits": [0, 1, 0, 1, 0, 1, 0, 1]}
```

Counter applies teleportation corrections and stores the ballot. (not implemented)

Counter -> voter:

```json
{"type": "result", "status": "accepted"}
```

Administrator -> Counter:

```json
{"type": "basis", "k": [0, 1, 1, 0]}
```

Counter measures each stored ballot in basis k. (not implemented)

Counter -> public:

```json
{"type": "tally", "counts": {"Candidate A": 2, "Candidate B": 1}}
```

Error responses at any step:

```json
{"type": "error", "message": "..."}
```
