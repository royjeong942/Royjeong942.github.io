# app.py — Markd Attendance Tracker
# Python 3.10+ | Flask | SQLite

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import sqlite3, hashlib, os, json
from datetime import datetime, date
from functools import wraps

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app, supports_credentials=True)

DB_PATH = os.path.join(os.path.dirname(__file__), "markd.db")


# ─────────────────────────────────────────────
#  DATABASE HELPERS
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def init_db():
    """Create tables and seed demo data if the DB doesn't exist yet."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            name        TEXT    NOT NULL,
            role        TEXT    NOT NULL CHECK(role IN ('admin','teacher','parent')),
            class_id    INTEGER REFERENCES classes(id),
            child_id    INTEGER REFERENCES students(id),
            color       TEXT    NOT NULL DEFAULT '#4361ee',
            initials    TEXT    NOT NULL DEFAULT '??'
        );

        CREATE TABLE IF NOT EXISTS classes (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT    NOT NULL,
            teacher TEXT    NOT NULL,
            subject TEXT    NOT NULL DEFAULT '',
            room    TEXT    NOT NULL DEFAULT '101'
        );

        CREATE TABLE IF NOT EXISTS blocks (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            num      INTEGER NOT NULL,
            label    TEXT    NOT NULL,
            room     TEXT    NOT NULL,
            UNIQUE(class_id, num)
        );

        CREATE TABLE IF NOT EXISTS students (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            name     TEXT    NOT NULL,
            student_code TEXT NOT NULL,
            grade    INTEGER NOT NULL DEFAULT 10,
            color    TEXT    NOT NULL DEFAULT '#4361ee'
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            block_id   INTEGER NOT NULL REFERENCES blocks(id)   ON DELETE CASCADE,
            date       TEXT    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'unmarked'
                               CHECK(status IN ('present','absent','skip','unmarked')),
            note       TEXT    NOT NULL DEFAULT '',
            UNIQUE(student_id, block_id, date)
        );

        CREATE TABLE IF NOT EXISTS session_notes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            date     TEXT    NOT NULL,
            notes    TEXT    NOT NULL DEFAULT '',
            UNIQUE(class_id, date)
        );
        """)

        # Seed if empty
        row = conn.execute("SELECT COUNT(*) as c FROM classes").fetchone()
        if row["c"] > 0:
            return

        # Classes
        c1 = conn.execute(
            "INSERT INTO classes(name,teacher,subject,room) VALUES(?,?,?,?)",
            ("Science 101","Mr. Thompson","Science","201")
        ).lastrowid
        c2 = conn.execute(
            "INSERT INTO classes(name,teacher,subject,room) VALUES(?,?,?,?)",
            ("Math 201","Ms. Chen","Mathematics","305")
        ).lastrowid

        # Blocks
        for cid, rooms in [(c1,["201","202","203","204","205"]),(c2,["305","306","307","308","309"])]:
            for i,r in enumerate(rooms,1):
                conn.execute(
                    "INSERT INTO blocks(class_id,num,label,room) VALUES(?,?,?,?)",
                    (cid, i, f"Block {i}", r)
                )

        # Students — Science 101
        science_students = [
            ("Ava Mitchell",    "S001", 10, "#4361ee"),
            ("Ethan Park",      "S002", 10, "#7b2d8b"),
            ("Sofia Reyes",     "S003", 9,  "#d63031"),
            ("Liam Chen",       "S004", 11, "#00897b"),
            ("Mia Johansson",   "S005", 9,  "#e67e22"),
            ("Noah Patel",      "S006", 12, "#2980b9"),
            ("Isabelle Martin", "S007", 11, "#c0392b"),
            ("Lucas Kim",       "S008", 10, "#1d7a43"),
        ]
        s_ids = {}
        for name, code, grade, color in science_students:
            sid = conn.execute(
                "INSERT INTO students(class_id,name,student_code,grade,color) VALUES(?,?,?,?,?)",
                (c1, name, code, grade, color)
            ).lastrowid
            s_ids[code] = sid

        # Students — Math 201
        math_students = [
            ("Emma Nguyen",     "M001", 12, "#6c3483"),
            ("James O'Brien",   "M002", 9,  "#b5770d"),
            ("Amelia Torres",   "M003", 11, "#117a65"),
            ("Oliver Huang",    "M004", 12, "#1a5276"),
            ("Charlotte Singh", "M005", 10, "#78281f"),
            ("Benjamin Müller", "M006", 9,  "#4a235a"),
            ("Harper Davis",    "M007", 11, "#145a32"),
        ]
        for name, code, grade, color in math_students:
            conn.execute(
                "INSERT INTO students(class_id,name,student_code,grade,color) VALUES(?,?,?,?,?)",
                (c2, name, code, grade, color)
            )

        # Users
        users = [
            ("mr.thompson", "teacher123", "Mr. Thompson", "teacher", c1,  None,          "#4361ee", "MT"),
            ("ms.chen",     "teacher123", "Ms. Chen",     "teacher", c2,  None,          "#00897b", "MC"),
            ("admin",       "admin123",   "Admin",        "admin",   None, None,          "#c0392b", "AD"),
            ("parent.mitchell","parent123","Mrs. Mitchell","parent",  c1,  s_ids["S001"], "#4361ee", "PM"),
            ("parent.park", "parent123",  "Mr. Park",     "parent",  c1,  s_ids["S002"], "#7b2d8b", "PP"),
        ]
        for uname, pw, name, role, cid, child, color, initials in users:
            conn.execute(
                "INSERT INTO users(username,password,name,role,class_id,child_id,color,initials) VALUES(?,?,?,?,?,?,?,?)",
                (uname, hash_password(pw), name, role, cid, child, color, initials)
            )

        conn.commit()
        print("✅  Database seeded with demo data")


# ─────────────────────────────────────────────
#  AUTH DECORATOR
# ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return wrapper


def get_current_user():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id=?", (session["user_id"],)
        ).fetchone()


# ─────────────────────────────────────────────
#  SERVE FRONTEND
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


# ─────────────────────────────────────────────
#  AUTH ROUTES
# ─────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = (data.get("username") or "").strip().lower()
    password  = data.get("password") or ""

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, hash_password(password))
        ).fetchone()

    if not user:
        return jsonify({"error": "Incorrect username or password"}), 401

    session.clear()
    session["user_id"]  = user["id"]
    session["role"]     = user["role"]
    session["class_id"] = user["class_id"]

    return jsonify({
        "id":        user["id"],
        "username":  user["username"],
        "name":      user["name"],
        "role":      user["role"],
        "class_id":  user["class_id"],
        "child_id":  user["child_id"],
        "color":     user["color"],
        "initials":  user["initials"],
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required
def me():
    user = get_current_user()
    return jsonify({
        "id":        user["id"],
        "username":  user["username"],
        "name":      user["name"],
        "role":      user["role"],
        "class_id":  user["class_id"],
        "child_id":  user["child_id"],
        "color":     user["color"],
        "initials":  user["initials"],
    })


# ─────────────────────────────────────────────
#  CLASSES
# ─────────────────────────────────────────────

def class_accessible(class_id):
    """Check that the logged-in user may access this class."""
    role = session.get("role")
    if role == "admin":
        return True
    return session.get("class_id") == class_id


@app.route("/api/classes")
@login_required
def get_classes():
    with get_db() as conn:
        if session["role"] == "admin":
            rows = conn.execute("SELECT * FROM classes ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM classes WHERE id=?", (session["class_id"],)
            ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/classes", methods=["POST"])
@login_required
@admin_required
def create_class():
    d = request.get_json()
    name    = d.get("name","").strip()
    teacher = d.get("teacher","TBD").strip()
    subject = d.get("subject","").strip()
    room    = d.get("room","101").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400

    with get_db() as conn:
        cid = conn.execute(
            "INSERT INTO classes(name,teacher,subject,room) VALUES(?,?,?,?)",
            (name, teacher, subject, room)
        ).lastrowid
        for i in range(1,6):
            conn.execute(
                "INSERT INTO blocks(class_id,num,label,room) VALUES(?,?,?,?)",
                (cid, i, f"Block {i}", room)
            )
        conn.commit()
        cls = dict(conn.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone())
    return jsonify(cls), 201


@app.route("/api/classes/<int:cid>", methods=["PATCH"])
@login_required
def update_class(cid):
    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    d = request.get_json()
    with get_db() as conn:
        cls = conn.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone()
        if not cls:
            return jsonify({"error": "Not found"}), 404
        conn.execute(
            "UPDATE classes SET name=?,teacher=?,subject=?,room=? WHERE id=?",
            (d.get("name", cls["name"]),
             d.get("teacher", cls["teacher"]),
             d.get("subject", cls["subject"]),
             d.get("room", cls["room"]), cid)
        )
        conn.commit()
        cls = dict(conn.execute("SELECT * FROM classes WHERE id=?", (cid,)).fetchone())
    return jsonify(cls)


@app.route("/api/classes/<int:cid>", methods=["DELETE"])
@login_required
@admin_required
def delete_class(cid):
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM classes").fetchone()["c"]
        if total <= 1:
            return jsonify({"error": "Cannot delete the only class"}), 400
        conn.execute("DELETE FROM classes WHERE id=?", (cid,))
        conn.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  BLOCKS
# ─────────────────────────────────────────────

@app.route("/api/classes/<int:cid>/blocks")
@login_required
def get_blocks(cid):
    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM blocks WHERE class_id=? ORDER BY num", (cid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/blocks/<int:bid>", methods=["PATCH"])
@login_required
def update_block(bid):
    d = request.get_json()
    with get_db() as conn:
        blk = conn.execute("SELECT * FROM blocks WHERE id=?", (bid,)).fetchone()
        if not blk or not class_accessible(blk["class_id"]):
            return jsonify({"error": "Forbidden"}), 403
        conn.execute(
            "UPDATE blocks SET label=?,room=? WHERE id=?",
            (d.get("label", blk["label"]), d.get("room", blk["room"]), bid)
        )
        conn.commit()
        blk = dict(conn.execute("SELECT * FROM blocks WHERE id=?", (bid,)).fetchone())
    return jsonify(blk)


# ─────────────────────────────────────────────
#  STUDENTS
# ─────────────────────────────────────────────

@app.route("/api/classes/<int:cid>/students")
@login_required
def get_students(cid):
    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM students WHERE class_id=? ORDER BY id", (cid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/classes/<int:cid>/students", methods=["POST"])
@login_required
def create_student(cid):
    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    d = request.get_json()
    name  = d.get("name","").strip()
    code  = d.get("student_code","").strip()
    grade = int(d.get("grade", 10))
    color = d.get("color","#4361ee")
    if not name or not code:
        return jsonify({"error": "Name and student code required"}), 400
    with get_db() as conn:
        dup = conn.execute(
            "SELECT id FROM students WHERE class_id=? AND student_code=?", (cid, code)
        ).fetchone()
        if dup:
            return jsonify({"error": "Student code already exists in this class"}), 409
        sid = conn.execute(
            "INSERT INTO students(class_id,name,student_code,grade,color) VALUES(?,?,?,?,?)",
            (cid, name, code, grade, color)
        ).lastrowid
        conn.commit()
        s = dict(conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
    return jsonify(s), 201


@app.route("/api/students/<int:sid>", methods=["PATCH"])
@login_required
def update_student(sid):
    with get_db() as conn:
        s = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        if not s or not class_accessible(s["class_id"]):
            return jsonify({"error": "Forbidden"}), 403
        d = request.get_json()
        conn.execute(
            "UPDATE students SET name=?,student_code=?,grade=?,color=? WHERE id=?",
            (d.get("name", s["name"]),
             d.get("student_code", s["student_code"]),
             d.get("grade", s["grade"]),
             d.get("color", s["color"]), sid)
        )
        conn.commit()
        s = dict(conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
    return jsonify(s)


@app.route("/api/students/<int:sid>", methods=["DELETE"])
@login_required
def delete_student(sid):
    with get_db() as conn:
        s = conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        if not s or not class_accessible(s["class_id"]):
            return jsonify({"error": "Forbidden"}), 403
        conn.execute("DELETE FROM students WHERE id=?", (sid,))
        conn.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  ATTENDANCE
# ─────────────────────────────────────────────

@app.route("/api/attendance")
@login_required
def get_attendance():
    """
    Query params: class_id, block_id, date (YYYY-MM-DD)
    Returns list of {student_id, block_id, date, status, note}
    """
    cid  = request.args.get("class_id", type=int)
    bid  = request.args.get("block_id",  type=int)
    d    = request.args.get("date", date.today().isoformat())

    if not cid or not class_accessible(cid):
        return jsonify({"error": "Forbidden or missing class_id"}), 403

    with get_db() as conn:
        query = """
            SELECT a.student_id, a.block_id, a.date, a.status, a.note
            FROM attendance a
            JOIN students s ON s.id = a.student_id
            WHERE s.class_id = ? AND a.date = ?
        """
        params = [cid, d]
        if bid:
            query += " AND a.block_id = ?"
            params.append(bid)
        rows = conn.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/attendance", methods=["POST"])
@login_required
def upsert_attendance():
    """
    Body: {student_id, block_id, date, status, note}
    Upserts a single attendance record.
    """
    d = request.get_json()
    student_id = d.get("student_id")
    block_id   = d.get("block_id")
    att_date   = d.get("date", date.today().isoformat())
    status     = d.get("status", "unmarked")
    note       = d.get("note", "")

    with get_db() as conn:
        # verify student belongs to a class this user can access
        s = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not s or not class_accessible(s["class_id"]):
            return jsonify({"error": "Forbidden"}), 403

        conn.execute("""
            INSERT INTO attendance(student_id,block_id,date,status,note)
            VALUES(?,?,?,?,?)
            ON CONFLICT(student_id,block_id,date)
            DO UPDATE SET status=excluded.status, note=excluded.note
        """, (student_id, block_id, att_date, status, note))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/attendance/bulk", methods=["POST"])
@login_required
def bulk_attendance():
    """
    Body: {class_id, block_id, date, status}
    Marks ALL students in the class with the given status.
    """
    d      = request.get_json()
    cid    = d.get("class_id")
    bid    = d.get("block_id")
    adate  = d.get("date", date.today().isoformat())
    status = d.get("status", "present")

    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403

    with get_db() as conn:
        students = conn.execute(
            "SELECT id FROM students WHERE class_id=?", (cid,)
        ).fetchall()
        for s in students:
            conn.execute("""
                INSERT INTO attendance(student_id,block_id,date,status,note)
                VALUES(?,?,?,?,'')
                ON CONFLICT(student_id,block_id,date)
                DO UPDATE SET status=excluded.status
            """, (s["id"], bid, adate, status))
        conn.commit()
    return jsonify({"ok": True, "updated": len(students)})


@app.route("/api/attendance/history/<int:student_id>")
@login_required
def student_history(student_id):
    """Return past attendance records for a student grouped by date, all blocks."""
    with get_db() as conn:
        s = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if not s or not class_accessible(s["class_id"]):
            return jsonify({"error": "Forbidden"}), 403

        # All blocks for this class
        all_blocks = conn.execute(
            "SELECT id, num FROM blocks WHERE class_id=? ORDER BY num",
            (s["class_id"],)
        ).fetchall()

        # All attendance records for this student (any status)
        rows = conn.execute("""
            SELECT a.date, a.status, a.note, b.num as block_num
            FROM attendance a
            JOIN blocks b ON b.id = a.block_id
            WHERE a.student_id = ?
            ORDER BY a.date DESC, b.num
        """, (student_id,)).fetchall()

        # Group by date
        by_date = {}
        for r in rows:
            if r["date"] not in by_date:
                by_date[r["date"]] = {}
            by_date[r["date"]][r["block_num"]] = {"status": r["status"], "note": r["note"]}

        # Build result: for each date, include all blocks (fill unmarked if missing)
        result = []
        for d in sorted(by_date.keys(), reverse=True):
            for blk in all_blocks:
                entry = by_date[d].get(blk["num"], {"status": "unmarked", "note": ""})
                result.append({
                    "date":      d,
                    "block_num": blk["num"],
                    "status":    entry["status"],
                    "note":      entry["note"],
                })

    return jsonify(result)


# ─────────────────────────────────────────────
#  SESSION NOTES
# ─────────────────────────────────────────────

@app.route("/api/notes")
@login_required
def get_notes():
    cid = request.args.get("class_id", type=int)
    d   = request.args.get("date", date.today().isoformat())
    if not cid or not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    with get_db() as conn:
        row = conn.execute(
            "SELECT notes FROM session_notes WHERE class_id=? AND date=?", (cid, d)
        ).fetchone()
    return jsonify({"notes": row["notes"] if row else ""})


@app.route("/api/notes", methods=["POST"])
@login_required
def save_notes():
    d = request.get_json()
    cid   = d.get("class_id")
    adate = d.get("date", date.today().isoformat())
    notes = d.get("notes", "")
    if not class_accessible(cid):
        return jsonify({"error": "Forbidden"}), 403
    with get_db() as conn:
        conn.execute("""
            INSERT INTO session_notes(class_id,date,notes) VALUES(?,?,?)
            ON CONFLICT(class_id,date) DO UPDATE SET notes=excluded.notes
        """, (cid, adate, notes))
        conn.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  USER / ACCOUNT MANAGEMENT (admin only)
# ─────────────────────────────────────────────

@app.route("/api/users")
@login_required
@admin_required
def get_users():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    result = []
    for r in rows:
        u = dict(r)
        del u["password"]
        result.append(u)
    return jsonify(result)


@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def create_user():
    d        = request.get_json()
    username = d.get("username","").strip().lower()
    password = d.get("password","").strip()
    name     = d.get("name","").strip()
    role     = d.get("role","teacher")
    class_id = d.get("class_id") or None
    child_id = d.get("child_id") or None
    color    = d.get("color","#4361ee")
    initials = d.get("initials","??")

    if not username or not password or not name:
        return jsonify({"error": "username, password, and name are required"}), 400

    with get_db() as conn:
        dup = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if dup:
            return jsonify({"error": "Username already exists"}), 409
        uid = conn.execute(
            "INSERT INTO users(username,password,name,role,class_id,child_id,color,initials) VALUES(?,?,?,?,?,?,?,?)",
            (username, hash_password(password), name, role, class_id, child_id, color, initials)
        ).lastrowid
        conn.commit()
        u = dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        del u["password"]
    return jsonify(u), 201


@app.route("/api/users/<int:uid>", methods=["PATCH"])
@login_required
@admin_required
def update_user(uid):
    d = request.get_json()
    with get_db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({"error": "Not found"}), 404
        new_pw = hash_password(d["password"]) if d.get("password") else u["password"]
        conn.execute("""
            UPDATE users SET name=?,password=?,role=?,class_id=?,child_id=?,color=?,initials=?
            WHERE id=?
        """, (
            d.get("name", u["name"]),
            new_pw,
            d.get("role", u["role"]),
            d.get("class_id") or None,
            d.get("child_id") or None,
            d.get("color", u["color"]),
            d.get("initials", u["initials"]),
            uid
        ))
        conn.commit()
        u = dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        del u["password"]
    return jsonify(u)


@app.route("/api/users/<int:uid>", methods=["DELETE"])
@login_required
@admin_required
def delete_user(uid):
    if uid == session["user_id"]:
        return jsonify({"error": "Cannot delete your own account"}), 400
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  PARENT — child data view
# ─────────────────────────────────────────────

@app.route("/api/parent/child")
@login_required
def parent_child():
    """Returns the child's full profile + today's attendance + recent history."""
    if session["role"] != "parent":
        return jsonify({"error": "Forbidden"}), 403

    user = get_current_user()
    child_id = user["child_id"]

    with get_db() as conn:
        child = conn.execute("SELECT * FROM students WHERE id=?", (child_id,)).fetchone()
        if not child:
            return jsonify({"error": "Child not found"}), 404

        cls = conn.execute("SELECT * FROM classes WHERE id=?", (child["class_id"],)).fetchone()
        blocks = conn.execute(
            "SELECT * FROM blocks WHERE class_id=? ORDER BY num", (child["class_id"],)
        ).fetchall()

        today = date.today().isoformat()
        today_att = conn.execute("""
            SELECT a.status, a.note, b.num as block_num
            FROM attendance a JOIN blocks b ON b.id=a.block_id
            WHERE a.student_id=? AND a.date=?
            ORDER BY b.num
        """, (child_id, today)).fetchall()

        history_rows = conn.execute("""
            SELECT a.date, a.status, a.note, b.num as block_num
            FROM attendance a
            JOIN blocks b ON b.id = a.block_id
            WHERE a.student_id = ?
            ORDER BY a.date DESC, b.num
        """, (child_id,)).fetchall()

        # Group by date, fill missing blocks as unmarked
        all_blocks_rows = conn.execute(
            "SELECT id, num FROM blocks WHERE class_id=? ORDER BY num",
            (child["class_id"],)
        ).fetchall()

        by_date = {}
        for r in history_rows:
            if r["date"] not in by_date:
                by_date[r["date"]] = {}
            by_date[r["date"]][r["block_num"]] = {"status": r["status"], "note": r["note"]}

        history = []
        for d in sorted(by_date.keys(), reverse=True):
            for blk in all_blocks_rows:
                entry = by_date[d].get(blk["num"], {"status": "unmarked", "note": ""})
                history.append({
                    "date":      d,
                    "block_num": blk["num"],
                    "status":    entry["status"],
                    "note":      entry["note"],
                })

        # summary counts
        counts = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM attendance WHERE student_id=? AND status!='unmarked'
            GROUP BY status
        """, (child_id,)).fetchall()

        # session notes for today
        note_row = conn.execute(
            "SELECT notes FROM session_notes WHERE class_id=? AND date=?",
            (child["class_id"], today)
        ).fetchone()

    cnt_map = {r["status"]: r["cnt"] for r in counts}
    total = sum(cnt_map.values())

    return jsonify({
        "child":     dict(child),
        "class":     dict(cls),
        "blocks":    [dict(b) for b in blocks],
        "today":     [dict(a) for a in today_att],
        "history":   history,
        "counts":    cnt_map,
        "rate":      round(cnt_map.get("present",0)/total*100) if total else None,
        "notes":     note_row["notes"] if note_row else "",
    })


# ─────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
