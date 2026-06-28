# This file handles the Flask GUI for the client (voter).  
# The quantum protocol for the voter is in client_backend.py.any

import argparse
import json
import logging
import sys
import threading
import uuid
from copy import deepcopy

from flask import Flask, jsonify, render_template, request, session

from client_backend import (
    CONNECTED,
    CONNECTING,
    DONE,
    FAILED,
    FETCH_FAILED,
    RECEIVING_BALLOT,
    SUBMITTING,
    WAIT_CANDIDATES,
    WAIT_RESULT,
    WAIT_VOTE,
    fetch_candidates,
    start_voter_client,
    vote_to_bits,
)

# Party string that the user can choose from in the GUI
from election import PARTIES

app = Flask(__name__)
app.secret_key = "dev"
_states: dict[str, dict] = {}
_lock = threading.Lock()

# Translate states to a more descriptive string
def _connection_line(state: str) -> str:
    if state == WAIT_CANDIDATES:
        admin, counter = "connecting", "idle"
    elif state == RECEIVING_BALLOT:
        admin, counter = "receiving ballot", "idle"
    elif state == FETCH_FAILED:
        admin, counter = "failed", "idle"
    elif state == WAIT_VOTE:
        admin, counter = "connected", "idle"
    elif state == CONNECTING:
        admin, counter = "connected", "connecting"
    elif state in (CONNECTED, SUBMITTING, WAIT_RESULT):
        admin, counter = "connected", "connected"
    elif state == DONE:
        admin, counter = "connected", "done"
    elif state == FAILED:
        admin, counter = "connected", "failed"
    else:
        admin, counter = "idle", "idle"
    return f"Administrator: {admin} | Counter: {counter}"


# Initial setup
def _new_ctx() -> dict:
    return {
        "party": PARTIES[0],
        "state": "IDLE",
        "status": "Select a party and connect.",
        "connection": _connection_line("IDLE"),
        "candidates": {},
        "result": "",
    }


def _get_ctx() -> dict:
    sid = session.get("sid")
    if not sid:
        sid = str(uuid.uuid4())
        session["sid"] = sid
    with _lock:
        if sid not in _states:
            _states[sid] = _new_ctx()
        return _states[sid]


# Update status line based on state
def _set_state(ctx: dict, state: str) -> None:
    ctx["state"] = state
    ctx["connection"] = _connection_line(state)
    if state == WAIT_VOTE:
        ctx["status"] = "Select a candidate and cast your vote."
    elif state == FETCH_FAILED:
        ctx["status"] = "Could not load candidates from Administrator."
    elif state == WAIT_CANDIDATES:
        ctx["status"] = "Loading candidates..."
    elif state == RECEIVING_BALLOT:
        ctx["status"] = "Receiving blank ballot from Administrator..."
    elif state == CONNECTING:
        ctx["status"] = "Connecting to Counter..."
    elif state == WAIT_RESULT:
        ctx["status"] = "Waiting for count result..."


def _format_election_status(data: dict, party: str) -> str:
    counts = data.get("counts", {})
    parts = [f"Counts: {counts}"]
    mine = data.get("votes", {}).get(party, {})
    if mine.get("accepted") and mine.get("candidate"):
        parts.append(f"Your vote: {mine['candidate']} ({mine.get('vote')})")
    elif mine.get("vote"):
        parts.append(f"Your vote rejected: {mine.get('vote')}")
    return " | ".join(parts)


def _apply_election_data(ctx: dict, data: dict) -> None:
    party = ctx.get("party", "")
    if party not in data.get("votes", {}):
        return
    if ctx["state"] in ("IDLE", "WAIT_CANDIDATES", "RECEIVING_BALLOT", "WAIT_VOTE", "FETCH_FAILED"):
        return
    ctx["state"] = DONE
    ctx["connection"] = _connection_line(DONE)
    ctx["status"] = _format_election_status(data, party)
    ctx["result"] = json.dumps(data)


def _handle_vote_result(ctx: dict, result: str) -> None:
    ctx["result"] = result
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        ctx["status"] = result
        return
    if data.get("status") == "accepted":
        _apply_election_data(ctx, data)
        return
    ctx["status"] = result


def _snapshot(ctx: dict) -> dict:
    with _lock:
        return deepcopy(ctx)


@app.route("/")
def index():
    ctx = _get_ctx()
    return render_template(
        "index.html",
        parties=PARTIES,
        party=ctx["party"],
        state=ctx["state"],
        connection=ctx["connection"],
        status=ctx["status"],
        candidates=ctx["candidates"],
        result=ctx["result"],
        ready=ctx["state"] == WAIT_VOTE,
        busy=ctx["state"]
        in (
            WAIT_CANDIDATES,
            RECEIVING_BALLOT,
            CONNECTING,
            CONNECTED,
            SUBMITTING,
            WAIT_RESULT,
        ),
    )


@app.route("/status")
def status():
    return jsonify(_snapshot(_get_ctx()))


# Start the protocol by receiving the ballot from the Administrator
@app.route("/connect", methods=["POST"])
def connect():
    ctx = _get_ctx()
    party = request.form.get("party", PARTIES[0])
    if party not in PARTIES:
        return jsonify({"error": "invalid party"}), 400

    with _lock:
        ctx["party"] = party
        ctx["candidates"] = {}
        ctx["result"] = ""
        _set_state(ctx, WAIT_CANDIDATES)

    def run() -> None:
        def on_state(state: str) -> None:
            with _lock:
                _set_state(ctx, state)

        try:
            candidates = fetch_candidates(party, on_state)
            with _lock:
                ctx["candidates"] = candidates
        except RuntimeError as exc:
            with _lock:
                ctx["status"] = str(exc)

    threading.Thread(target=run, daemon=True).start()
    return jsonify(_snapshot(ctx))


# Start the voting procedure (submitting the ballot to the Counter)
@app.route("/vote", methods=["POST"])
def vote():
    ctx = _get_ctx()
    with _lock:
        if ctx["state"] != WAIT_VOTE:
            return jsonify({"error": "not ready"}), 400
        candidate = request.form.get("candidate", "")
        if candidate not in ctx["candidates"]:
            return jsonify({"error": "invalid candidate"}), 400
        party = ctx["party"]
        vote_bits = vote_to_bits(candidate, ctx["candidates"])
        ctx["status"] = f"Submitting vote for {candidate} ({vote_bits})..."

    def run() -> None:
        def on_state(state: str) -> None:
            with _lock:
                _set_state(ctx, state)

        def on_result(result: str) -> None:
            with _lock:
                _handle_vote_result(ctx, result)

        start_voter_client(party, vote_bits, on_state, on_result)

    threading.Thread(target=run, daemon=True).start()
    return jsonify(_snapshot(ctx))


class _SkipStatusPollLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /status " not in record.getMessage()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quantum voting web client")
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="HTTP port (default: 5000)",
    )
    args = parser.parse_args()

    logging.getLogger("werkzeug").addFilter(_SkipStatusPollLog())
    print(f"Starting client on http://127.0.0.1:{args.port}", file=sys.stderr, flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
