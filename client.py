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
    FETCHING_CANDIDATES,
    READY,
    SUBMITTING,
    fetch_candidates,
    start_voter_client,
    vote_to_bits,
)

PARTIES = ["Voter1", "Voter2", "Voter3"]

app = Flask(__name__)
app.secret_key = "dev"
_states: dict[str, dict] = {}
_lock = threading.Lock()


def _connection_line(state: str) -> str:
    if state == FETCHING_CANDIDATES:
        admin, counter = "connecting", "idle"
    elif state == FETCH_FAILED:
        admin, counter = "failed", "idle"
    elif state == READY:
        admin, counter = "connected", "idle"
    elif state == CONNECTING:
        admin, counter = "connected", "connecting"
    elif state in (CONNECTED, SUBMITTING):
        admin, counter = "connected", "connected"
    elif state == DONE:
        admin, counter = "connected", "done"
    elif state == FAILED:
        admin, counter = "connected", "failed"
    else:
        admin, counter = "idle", "idle"
    return f"Administrator: {admin} | Counter: {counter}"


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


def _set_state(ctx: dict, state: str) -> None:
    ctx["state"] = state
    ctx["connection"] = _connection_line(state)
    if state == READY:
        ctx["status"] = "Select a candidate and cast your vote."
    elif state == FETCH_FAILED:
        ctx["status"] = "Could not load candidates from Administrator."
    elif state == FETCHING_CANDIDATES:
        ctx["status"] = "Loading candidates..."
    elif state == CONNECTING:
        ctx["status"] = "Connecting to Counter..."


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
        ready=ctx["state"] == READY,
        busy=ctx["state"] in (FETCHING_CANDIDATES, CONNECTING, CONNECTED, SUBMITTING),
    )


@app.route("/status")
def status():
    return jsonify(_snapshot(_get_ctx()))


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
        _set_state(ctx, FETCHING_CANDIDATES)

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


@app.route("/vote", methods=["POST"])
def vote():
    ctx = _get_ctx()
    with _lock:
        if ctx["state"] != READY:
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
                ctx["result"] = result
                ctx["status"] = result

        start_voter_client(party, vote_bits, on_state, on_result)

    threading.Thread(target=run, daemon=True).start()
    return jsonify(_snapshot(ctx))


class _SkipStatusPollLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /status " not in record.getMessage()


if __name__ == "__main__":
    logging.getLogger("werkzeug").addFilter(_SkipStatusPollLog())
    print("Starting client.", file=sys.stderr, flush=True)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
