"""SQLite persistence: settings, reference database, profiles, inspections and measurements."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manufacturers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT, website TEXT, notes TEXT,
    builtin INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS reference_records (
    id TEXT PRIMARY KEY, manufacturer_id TEXT, part_number TEXT, base_part TEXT, category TEXT,
    data TEXT NOT NULL, builtin INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL, builtin INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS sim_profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT, reference_id TEXT, param TEXT,
    mean REAL, std REAL, n INTEGER, unit TEXT, conditions TEXT, data_source TEXT,
    source_inspection_id TEXT, extra TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS inspections (
    id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, status TEXT, data_source TEXT,
    profile_id TEXT, reference_id TEXT, part_number TEXT, manufacturer TEXT, component_name TEXT,
    mode TEXT, overall REAL, result TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, inspection_id TEXT, phase TEXT,
    seq INTEGER, t_ms INTEGER, iso TEXT, source TEXT, dev_ms INTEGER, bus_v REAL, shunt_mv REAL,
    current_ma REAL, power_mw REAL, sensor TEXT, valid INTEGER, reason TEXT);
CREATE INDEX IF NOT EXISTS idx_meas_insp ON measurements(inspection_id);
CREATE INDEX IF NOT EXISTS idx_meas_session ON measurements(session_id, t_ms);
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, inspection_id TEXT, path TEXT, created_at TEXT, data_source TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _load_seed(name):
    with open(os.path.join(config.SEED_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def init_db():
    with _write_lock:
        conn = connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            ts = now_iso()
            # Manufacturers and references: insert built-ins only if missing (user edits are kept).
            for m in _load_seed("manufacturers.json"):
                conn.execute(
                    "INSERT OR IGNORE INTO manufacturers(id,name,aliases,website,notes,builtin,created_at) VALUES(?,?,?,?,?,1,?)",
                    (m["id"], m["name"], json.dumps(m.get("aliases", [])), m.get("website", ""), m.get("notes", ""), ts))
            for r in _load_seed("references.json"):
                r.setdefault("dataset_version", config.REFERENCE_DATASET_VERSION)
                conn.execute(
                    "INSERT OR IGNORE INTO reference_records(id,manufacturer_id,part_number,base_part,category,data,builtin,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,1,?,?)",
                    (r["id"], r["manufacturer_id"], r["part_number"], r["base_part"], r["category"], json.dumps(r), ts, ts))
            # Built-in profiles and simulation profiles are code-coupled: always refresh.
            for p in _load_seed("profiles.json"):
                conn.execute("INSERT OR REPLACE INTO profiles(id,data,builtin,created_at) VALUES(?,?,1,?)",
                             (p["id"], json.dumps(p), ts))
            conn.execute("DELETE FROM sim_profiles")
            for s in _load_seed("simulation_profiles.json"):
                conn.execute("INSERT INTO sim_profiles(id,data) VALUES(?,?)", (s["id"], json.dumps(s)))
            conn.commit()
        finally:
            conn.close()


def restore_builtin_references():
    with _write_lock:
        conn = connect()
        try:
            ts = now_iso()
            for m in _load_seed("manufacturers.json"):
                conn.execute(
                    "INSERT OR REPLACE INTO manufacturers(id,name,aliases,website,notes,builtin,created_at) VALUES(?,?,?,?,?,1,?)",
                    (m["id"], m["name"], json.dumps(m.get("aliases", [])), m.get("website", ""), m.get("notes", ""), ts))
            for r in _load_seed("references.json"):
                r.setdefault("dataset_version", config.REFERENCE_DATASET_VERSION)
                conn.execute(
                    "INSERT OR REPLACE INTO reference_records(id,manufacturer_id,part_number,base_part,category,data,builtin,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,1,?,?)",
                    (r["id"], r["manufacturer_id"], r["part_number"], r["base_part"], r["category"], json.dumps(r), ts, ts))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- settings
def get_settings():
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='app'").fetchone()
    finally:
        conn.close()
    stored = json.loads(row["value"]) if row else {}
    return config.deep_merge(config.DEFAULT_SETTINGS, stored)


def save_settings(new_settings):
    merged = config.deep_merge(get_settings(), new_settings)
    with _write_lock:
        conn = connect()
        try:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('app',?)", (json.dumps(merged),))
            conn.commit()
        finally:
            conn.close()
    return merged


# ---------------------------------------------------------------- manufacturers / references
def _mfr_row(row):
    return {"id": row["id"], "name": row["name"], "aliases": json.loads(row["aliases"] or "[]"),
            "website": row["website"], "notes": row["notes"], "builtin": bool(row["builtin"])}


def list_manufacturers():
    conn = connect()
    try:
        return [_mfr_row(r) for r in conn.execute("SELECT * FROM manufacturers ORDER BY name")]
    finally:
        conn.close()


def get_manufacturer(mid):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM manufacturers WHERE id=?", (mid,)).fetchone()
        return _mfr_row(r) if r else None
    finally:
        conn.close()


def save_manufacturer(m):
    with _write_lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO manufacturers(id,name,aliases,website,notes,builtin,created_at) "
                "VALUES(?,?,?,?,?,COALESCE((SELECT builtin FROM manufacturers WHERE id=?),0),?)",
                (m["id"], m["name"], json.dumps(m.get("aliases", [])), m.get("website", ""), m.get("notes", ""), m["id"], now_iso()))
            conn.commit()
        finally:
            conn.close()
    return get_manufacturer(m["id"])


def _ref_row(row):
    d = json.loads(row["data"])
    d["builtin"] = bool(row["builtin"])
    d["updated_at"] = row["updated_at"]
    return d


def list_references():
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM reference_records ORDER BY base_part, manufacturer_id").fetchall()
    finally:
        conn.close()
    mfrs = {m["id"]: m for m in list_manufacturers()}
    out = []
    for r in rows:
        d = _ref_row(r)
        d["manufacturer"] = mfrs.get(d.get("manufacturer_id"), {}).get("name", d.get("manufacturer_id"))
        out.append(d)
    return out


def get_reference(rid):
    if not rid:
        return None
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM reference_records WHERE id=?", (rid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = _ref_row(r)
    m = get_manufacturer(d.get("manufacturer_id"))
    d["manufacturer"] = m["name"] if m else d.get("manufacturer_id")
    return d


def save_reference(rec):
    ts = now_iso()
    with _write_lock:
        conn = connect()
        try:
            existing = conn.execute("SELECT builtin, created_at FROM reference_records WHERE id=?", (rec["id"],)).fetchone()
            builtin = existing["builtin"] if existing else 0
            created = existing["created_at"] if existing else ts
            clean = {k: v for k, v in rec.items() if k not in ("builtin", "updated_at", "manufacturer")}
            conn.execute(
                "INSERT OR REPLACE INTO reference_records(id,manufacturer_id,part_number,base_part,category,data,builtin,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (rec["id"], rec.get("manufacturer_id"), rec.get("part_number"), rec.get("base_part"), rec.get("category"),
                 json.dumps(clean), builtin, created, ts))
            conn.commit()
        finally:
            conn.close()
    return get_reference(rec["id"])


def delete_reference(rid):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM reference_records WHERE id=? AND builtin=0", (rid,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- profiles
def list_profiles():
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM profiles ORDER BY builtin DESC, id").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        d["builtin"] = bool(r["builtin"])
        out.append(d)
    return out


def get_profile(pid):
    if not pid:
        return None
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = json.loads(r["data"])
    d["builtin"] = bool(r["builtin"])
    return d


def save_profile(p):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("INSERT OR REPLACE INTO profiles(id,data,builtin,created_at) VALUES(?,?,0,?)",
                         (p["id"], json.dumps(p), now_iso()))
            conn.commit()
        finally:
            conn.close()
    return get_profile(p["id"])


def delete_profile(pid):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM profiles WHERE id=? AND builtin=0", (pid,))
            conn.commit()
        finally:
            conn.close()


def list_sim_profiles():
    conn = connect()
    try:
        return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM sim_profiles ORDER BY rowid")]
    finally:
        conn.close()


def get_sim_profile(sid):
    for s in list_sim_profiles():
        if s["id"] == sid:
            return s
    return None


# ---------------------------------------------------------------- baselines
def add_baseline(b):
    with _write_lock:
        conn = connect()
        try:
            cur = conn.execute(
                "INSERT INTO baselines(profile_id,reference_id,param,mean,std,n,unit,conditions,data_source,source_inspection_id,extra,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (b["profile_id"], b.get("reference_id"), b["param"], b.get("mean"), b.get("std"), b.get("n"), b.get("unit"),
                 json.dumps(b.get("conditions", {})), b["data_source"], b.get("source_inspection_id"),
                 json.dumps(b.get("extra", {})), now_iso()))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def list_baselines(profile_id=None, data_source=None):
    q = "SELECT * FROM baselines WHERE 1=1"
    args = []
    if profile_id:
        q += " AND profile_id=?"
        args.append(profile_id)
    if data_source:
        q += " AND data_source=?"
        args.append(data_source)
    q += " ORDER BY id DESC"
    conn = connect()
    try:
        rows = conn.execute(q, args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["conditions"] = json.loads(d["conditions"] or "{}")
        d["extra"] = json.loads(d["extra"] or "{}")
        out.append(d)
    return out


def delete_baseline(bid):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM baselines WHERE id=?", (bid,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- inspections
def next_inspection_id():
    day = datetime.now().strftime("%Y%m%d")
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM inspections WHERE id LIKE ?", (f"ICV-{day}-%",)).fetchone()
        n = row["n"] + 1
        while conn.execute("SELECT 1 FROM inspections WHERE id=?", (f"ICV-{day}-{n:04d}",)).fetchone():
            n += 1
    finally:
        conn.close()
    return f"ICV-{day}-{n:04d}"


def save_inspection(ins):
    ins["updated_at"] = now_iso()
    comp = ins.get("component", {})
    scores = ins.get("scores") or {}
    with _write_lock:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO inspections(id,created_at,updated_at,status,data_source,profile_id,reference_id,part_number,"
                "manufacturer,component_name,mode,overall,result,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ins["id"], ins["created_at"], ins["updated_at"], ins.get("status"), ins.get("data_source"), ins.get("profile_id"),
                 ins.get("reference_id"), comp.get("part_number"), comp.get("manufacturer"), comp.get("name"),
                 ins.get("config", {}).get("mode"), scores.get("overall"), scores.get("classification"), json.dumps(ins)))
            conn.commit()
        finally:
            conn.close()
    return ins


def get_inspection(iid):
    conn = connect()
    try:
        r = conn.execute("SELECT data FROM inspections WHERE id=?", (iid,)).fetchone()
    finally:
        conn.close()
    return json.loads(r["data"]) if r else None


def list_inspections(q=None, profile_id=None, manufacturer=None, status=None, result=None,
                     date_from=None, date_to=None, include_drafts=True, limit=500):
    sql = ("SELECT id,created_at,updated_at,status,data_source,profile_id,reference_id,part_number,manufacturer,"
           "component_name,mode,overall,result FROM inspections WHERE 1=1")
    args = []
    if q:
        sql += " AND (id LIKE ? OR part_number LIKE ? OR manufacturer LIKE ? OR component_name LIKE ?)"
        args += [f"%{q}%"] * 4
    if profile_id:
        sql += " AND profile_id=?"
        args.append(profile_id)
    if manufacturer:
        sql += " AND manufacturer=?"
        args.append(manufacturer)
    if status:
        sql += " AND status=?"
        args.append(status)
    if result:
        sql += " AND result=?"
        args.append(result)
    if date_from:
        sql += " AND created_at >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        args.append(date_to + "T23:59:59.999Z" if len(date_to) == 10 else date_to)
    if not include_drafts:
        sql += " AND status != 'draft'"
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def delete_inspection(iid):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("DELETE FROM inspections WHERE id=?", (iid,))
            conn.execute("DELETE FROM measurements WHERE inspection_id=?", (iid,))
            conn.execute("DELETE FROM reports WHERE inspection_id=?", (iid,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- measurements
MEAS_COLS = ("session_id", "inspection_id", "phase", "seq", "t_ms", "iso", "source", "dev_ms", "bus_v",
             "shunt_mv", "current_ma", "power_mw", "sensor", "valid", "reason")


def insert_measurements(rows):
    if not rows:
        return
    with _write_lock:
        conn = connect()
        try:
            conn.executemany(
                f"INSERT INTO measurements({','.join(MEAS_COLS)}) VALUES({','.join('?' * len(MEAS_COLS))})",
                [tuple(r.get(c) for c in MEAS_COLS) for r in rows])
            conn.commit()
        finally:
            conn.close()


def get_measurements(inspection_id=None, session_id=None, since_ms=None, limit=200000):
    sql = "SELECT * FROM measurements WHERE 1=1"
    args = []
    if inspection_id:
        sql += " AND inspection_id=?"
        args.append(inspection_id)
    if session_id:
        sql += " AND session_id=?"
        args.append(session_id)
    if since_ms:
        sql += " AND t_ms >= ?"
        args.append(since_ms)
    sql += " ORDER BY t_ms, id LIMIT ?"
    args.append(limit)
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------- reports
def add_report(rep):
    with _write_lock:
        conn = connect()
        try:
            conn.execute("INSERT OR REPLACE INTO reports(id,inspection_id,path,created_at,data_source) VALUES(?,?,?,?,?)",
                         (rep["id"], rep["inspection_id"], rep["path"], now_iso(), rep.get("data_source")))
            conn.commit()
        finally:
            conn.close()


def list_reports():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()]
    finally:
        conn.close()


def get_report_for(inspection_id):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM reports WHERE inspection_id=? ORDER BY created_at DESC", (inspection_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()
