import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from db import get_conn, init_db, now_iso

APP_DIR = Path(__file__).resolve().parent

LOGIN_USER = os.environ.get("STATION_USER", "admin")
LOGIN_PASS = os.environ.get("STATION_PASS", "gaming123")
SESSION_SECRET = os.environ.get("STATION_SECRET", secrets.token_hex(32))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=60 * 60 * 24 * 30)

init_db()


def require_login(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=401, detail="Not logged in")


def row_to_console(r):
    return {
        "id": r["id"], "name": r["name"], "type": r["type"], "hourlyRate": r["hourly_rate"],
        "status": r["status"], "order": r["sort_order"],
    }


def row_to_time_option(r):
    return {"id": r["id"], "label": r["label"], "minutes": r["minutes"]}


def row_to_addon(r):
    return {"id": r["id"], "name": r["name"], "amount": r["amount"], "consoleType": r["console_type"]}


def row_to_session(r):
    return {
        "id": r["id"], "dayKey": r["day_key"], "consoleId": r["console_id"], "consoleName": r["console_name"],
        "consoleType": r["console_type"], "customerName": r["customer_name"], "phone": r["phone"],
        "gameName": r["game_name"], "ratePerHour": r["rate_per_hour"], "durationMinutes": r["duration_minutes"],
        "addOns": json.loads(r["add_ons"]), "amountDue": r["amount_due"], "amountPaid": r["amount_paid"],
        "paymentStatus": r["payment_status"], "paymentMethod": r["payment_method"], "status": r["status"],
        "startTime": r["start_time"], "endTime": r["end_time"], "actualEndTime": r["actual_end_time"],
        "extensions": json.loads(r["extensions"]), "createdAt": r["created_at"],
    }


# ---------- auth ----------
@app.post("/api/login")
def login(request: Request, body: dict):
    username = body.get("username", "")
    password = body.get("password", "")
    ok = hmac.compare_digest(username, LOGIN_USER) and hmac.compare_digest(password, LOGIN_PASS)
    if not ok:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    request.session["logged_in"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"loggedIn": bool(request.session.get("logged_in"))}


# ---------- state ----------
@app.get("/api/state", dependencies=[Depends(require_login)])
def get_state():
    conn = get_conn()
    try:
        consoles = [row_to_console(r) for r in conn.execute("SELECT * FROM consoles ORDER BY sort_order")]
        time_options = [row_to_time_option(r) for r in conn.execute(
            "SELECT * FROM time_options WHERE active=1 ORDER BY minutes")]
        add_ons = [row_to_addon(r) for r in conn.execute("SELECT * FROM add_ons WHERE active=1 ORDER BY name")]
        running = [row_to_session(r) for r in conn.execute("SELECT * FROM sessions WHERE status='running'")]
        today_key = datetime.now().strftime("%Y-%m-%d")
        today = [row_to_session(r) for r in conn.execute(
            "SELECT * FROM sessions WHERE day_key=? ORDER BY created_at DESC LIMIT 500", (today_key,))]
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings")}
        has_qr = conn.execute("SELECT 1 FROM qr_image WHERE id=1").fetchone()
        settings["paymentQrPath"] = "/api/qr-image" if has_qr else ""
        return {
            "consoles": consoles, "timeOptions": time_options, "addOns": add_ons,
            "runningSessions": running, "todaySessions": today, "settings": settings,
        }
    finally:
        conn.close()


# ---------- consoles ----------
@app.post("/api/consoles", dependencies=[Depends(require_login)])
def add_console(body: dict):
    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) m FROM consoles").fetchone()["m"]
        conn.execute(
            "INSERT INTO consoles (name, type, hourly_rate, sort_order, status, created_at) VALUES (?, ?, ?, ?, 'available', ?)",
            (body["name"], body.get("type", ""), float(body.get("hourlyRate", 100)), max_order + 1, now_iso()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/consoles/{console_id}", dependencies=[Depends(require_login)])
def update_console(console_id: int, body: dict):
    conn = get_conn()
    try:
        fields, values = [], []
        for key, col in [("name", "name"), ("type", "type"), ("hourlyRate", "hourly_rate"), ("status", "status")]:
            if key in body:
                fields.append(f"{col}=?")
                values.append(body[key])
        if fields:
            values.append(console_id)
            conn.execute(f"UPDATE consoles SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/consoles/{console_id}", dependencies=[Depends(require_login)])
def delete_console(console_id: int):
    conn = get_conn()
    try:
        running = conn.execute("SELECT 1 FROM sessions WHERE console_id=? AND status='running'", (console_id,)).fetchone()
        if running:
            raise HTTPException(status_code=400, detail="Console has a running session")
        conn.execute("DELETE FROM consoles WHERE id=?", (console_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- time options ----------
@app.post("/api/time-options", dependencies=[Depends(require_login)])
def add_time_option(body: dict):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO time_options (label, minutes) VALUES (?, ?)", (body["label"], int(body["minutes"])))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/time-options/{opt_id}", dependencies=[Depends(require_login)])
def delete_time_option(opt_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM time_options WHERE id=?", (opt_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- add-ons ----------
@app.post("/api/addons", dependencies=[Depends(require_login)])
def add_addon(body: dict):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO add_ons (name, amount, console_type) VALUES (?, ?, ?)",
            (body["name"], float(body["amount"]), body.get("consoleType") or None),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/addons/{addon_id}", dependencies=[Depends(require_login)])
def delete_addon(addon_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM add_ons WHERE id=?", (addon_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- sessions ----------
@app.post("/api/sessions/start", dependencies=[Depends(require_login)])
def start_session(body: dict):
    conn = get_conn()
    try:
        console = conn.execute("SELECT * FROM consoles WHERE id=?", (body["consoleId"],)).fetchone()
        if not console:
            raise HTTPException(status_code=404, detail="Console not found")

        rate = float(body.get("ratePerHour", console["hourly_rate"]))
        minutes = int(body["durationMinutes"])
        add_ons = body.get("addOns", [])
        addon_total = sum(float(a.get("amount", 0)) for a in add_ons)
        amount_due = round(rate / 60 * minutes + addon_total, 2)

        now = datetime.now(timezone.utc)
        end = now.timestamp() + minutes * 60
        pay_now = bool(body.get("payNow"))

        conn.execute(
            """INSERT INTO sessions (day_key, console_id, console_name, console_type, customer_name, phone,
               game_name, rate_per_hour, duration_minutes, add_ons, amount_due, amount_paid, payment_status,
               payment_method, status, start_time, end_time, actual_end_time, extensions, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().strftime("%Y-%m-%d"), console["id"], console["name"], console["type"],
                body["customerName"], body.get("phone", ""), body.get("gameName", ""), rate, minutes,
                json.dumps(add_ons), amount_due, amount_due if pay_now else 0,
                "paid" if pay_now else "unpaid", body.get("method") if pay_now else None,
                "running", now.isoformat(), datetime.fromtimestamp(end, tz=timezone.utc).isoformat(), None,
                "[]", now.isoformat(),
            ),
        )
        conn.execute("UPDATE consoles SET status='occupied' WHERE id=?", (console["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/extend", dependencies=[Depends(require_login)])
def extend_session(session_id: int, body: dict):
    conn = get_conn()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        minutes = int(body["minutes"])
        extra = round(sess["rate_per_hour"] / 60 * minutes, 2)
        new_end = datetime.fromisoformat(sess["end_time"]).timestamp() + minutes * 60
        exts = json.loads(sess["extensions"])
        exts.append({"minutes": minutes, "amount": extra, "at": now_iso()})
        conn.execute(
            "UPDATE sessions SET end_time=?, duration_minutes=duration_minutes+?, amount_due=amount_due+?, extensions=? WHERE id=?",
            (datetime.fromtimestamp(new_end, tz=timezone.utc).isoformat(), minutes, extra, json.dumps(exts), session_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/pay", dependencies=[Depends(require_login)])
def pay_session(session_id: int, body: dict):
    conn = get_conn()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        amt = float(body.get("amount", 0))
        total_paid = round(sess["amount_paid"] + amt, 2)
        status = "unpaid" if total_paid <= 0 else ("partial" if total_paid < sess["amount_due"] else "paid")
        conn.execute(
            "UPDATE sessions SET amount_paid=?, payment_status=?, payment_method=? WHERE id=?",
            (total_paid, status, body.get("method"), session_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/end", dependencies=[Depends(require_login)])
def end_session(session_id: int, body: dict):
    conn = get_conn()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        collect = float(body.get("collectAmount", 0))
        total_paid = round(sess["amount_paid"] + collect, 2)
        status = "unpaid" if total_paid <= 0 else ("partial" if total_paid < sess["amount_due"] else "paid")
        method = body.get("method") if collect > 0 else sess["payment_method"]
        conn.execute(
            "UPDATE sessions SET status='completed', actual_end_time=?, amount_paid=?, payment_status=?, payment_method=? WHERE id=?",
            (now_iso(), total_paid, status, method, session_id),
        )
        conn.execute("UPDATE consoles SET status='available' WHERE id=?", (sess["console_id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/cancel", dependencies=[Depends(require_login)])
def cancel_session(session_id: int):
    conn = get_conn()
    try:
        sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found")
        conn.execute("UPDATE sessions SET status='cancelled', actual_end_time=? WHERE id=?", (now_iso(), session_id))
        conn.execute("UPDATE consoles SET status='available' WHERE id=?", (sess["console_id"],))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- settings ----------
@app.put("/api/settings", dependencies=[Depends(require_login)])
def update_settings(body: dict):
    conn = get_conn()
    try:
        for key in ("cafeName", "currencySymbol"):
            if key in body:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, body[key]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/settings/qr", dependencies=[Depends(require_login)])
async def upload_qr(file: UploadFile = File(...)):
    data = await file.read()
    content_type = file.content_type or "image/png"
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO qr_image (id, data, content_type) VALUES (1, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET data=excluded.data, content_type=excluded.content_type",
            (psycopg2.Binary(data), content_type),
        )
        conn.commit()
        return {"ok": True, "path": "/api/qr-image"}
    finally:
        conn.close()


@app.delete("/api/settings/qr", dependencies=[Depends(require_login)])
def delete_qr():
    conn = get_conn()
    try:
        conn.execute("DELETE FROM qr_image WHERE id=1")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/qr-image", dependencies=[Depends(require_login)])
def get_qr_image():
    conn = get_conn()
    try:
        row = conn.execute("SELECT data, content_type FROM qr_image WHERE id=1").fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No QR uploaded")
        return Response(content=bytes(row["data"]), media_type=row["content_type"])
    finally:
        conn.close()


@app.get("/")
def serve_index():
    return FileResponse(str(APP_DIR / "static" / "index.html"))


@app.get("/{path:path}")
def serve_static_catchall(path: str):
    candidate = APP_DIR / "static" / path
    if candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(APP_DIR / "static" / "index.html"))
