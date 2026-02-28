# server.py
from flask import Flask, request, jsonify
import sqlite3
import json
from datetime import datetime
from pathlib import Path

app = Flask(__name__)
DB_FILE = Path(__file__).with_name("ws_logs.db")


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ws_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinics TEXT,
                lab TEXT,
                action TEXT,
                send INTEGER DEFAULT 0,   -- 1=sent, 0=pending/fail
                payload TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()


def parse_payload():
    if request.is_json:
        return request.get_json(silent=True) or {}
    if request.form:
        return request.form.to_dict()
    # fallback raw body (xml/text)
    raw = request.get_data(as_text=True) or ""
    return {"raw": raw}


def insert_log(data, action):
    clinics = data.get("clinics") or data.get("clinic") or data.get("cli_ID") or ""
    lab = data.get("lab") or data.get("lab_id") or ""
    payload_text = json.dumps(data, ensure_ascii=False)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO ws_logs (clinics, lab, action, send, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(clinics), str(lab), action, 1, payload_text, now),
        )
        conn.commit()
        return cur.lastrowid


@app.post("/wsdl/req")
def wsdl_req():
    data = parse_payload()
    log_id = insert_log(data, "Send Request")
    # format phản hồi phù hợp client PHP (status + ID)
    return jsonify({"status": "OK", "ID": log_id})


@app.post("/wsdl/result")
def wsdl_result():
    data = parse_payload()
    log_id = insert_log(data, "Send Result")
    return jsonify({"status": "OK", "ID": log_id})


@app.get("/logs")
def logs():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, clinics, lab, action, send, created_at FROM ws_logs ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
