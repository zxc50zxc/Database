"""
SQLite database layer: schema, CRUD, seed data, and transactional appointment operations.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from models import (
    SEED_VERSION,
    AppointmentStatus,
    UserRole,
    VisitReason,
)

# Default DB path next to this package
DB_PATH = Path(__file__).resolve().parent / "appointments.db"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(
    db_path: Path | str | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT
        );

        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            specialty TEXT
        );

        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL,
            patient_id INTEGER REFERENCES patients(id),
            doctor_id INTEGER REFERENCES doctors(id)
        );

        CREATE TABLE IF NOT EXISTS doctor_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doctor_id INTEGER NOT NULL REFERENCES doctors(id),
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL REFERENCES patients(id),
            doctor_id INTEGER NOT NULL REFERENCES doctors(id),
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            start_at TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_appt_doctor_start
            ON appointments(doctor_id, start_at);
        CREATE INDEX IF NOT EXISTS idx_appt_patient_start
            ON appointments(patient_id, start_at);
        CREATE INDEX IF NOT EXISTS idx_appt_start
            ON appointments(start_at);
        """
    )
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def seed_demo_data(conn: sqlite3.Connection, auth_hash_fn) -> None:
    """
    Idempotent seed: runs once per SEED_VERSION.
    auth_hash_fn: callable(password) -> (hash_hex, salt_hex) from auth module.
    """
    current = _get_meta(conn, "seed_version")
    if current is not None and int(current) >= SEED_VERSION:
        return
    clinic_count = conn.execute("SELECT COUNT(*) AS c FROM clinics").fetchone()["c"]
    if clinic_count > 0:
        _set_meta(conn, "seed_version", str(SEED_VERSION))
        conn.commit()
        return

    now = datetime.now().replace(microsecond=0)

    clinics_data = [
        ("Al-Noor General Clinic", "Riyadh — Al-Malaz"),
        ("Al-Shifa Cardiology Center", "Jeddah — Al-Rawdah"),
        ("Family Care Polyclinic", "Dammam — Al-Faisaliyah"),
        ("Pediatric Health Hub", "Khobar — Al-Ulaya"),
        ("Orthopedic Specialists Clinic", "Riyadh — Olaya"),
    ]
    conn.executemany(
        "INSERT INTO clinics (name, address) VALUES (?, ?)",
        clinics_data,
    )

    doctors_data = [
        ("Dr. Ahmed Al-Harbi", 1, "Internal Medicine"),
        ("Dr. Sarah Al-Qahtani", 2, "Cardiology"),
        ("Dr. Omar Al-Zahrani", 3, "Family Medicine"),
        ("Dr. Layla Al-Mutairi", 4, "Pediatrics"),
        ("Dr. Khalid Al-Dosari", 5, "Orthopedics"),
    ]
    conn.executemany(
        "INSERT INTO doctors (name, clinic_id, specialty) VALUES (?, ?, ?)",
        doctors_data,
    )

    patients = []
    for i in range(1, 21):
        patients.append(
            (
                f"Patient Demo {i:02d}",
                f"050{1000000 + i:07d}",
                f"patient{i:02d}@demo.local",
                now.isoformat(),
            )
        )
    conn.executemany(
        "INSERT INTO patients (name, phone, email, created_at) VALUES (?, ?, ?, ?)",
        patients,
    )

    # Weekly availability Sun–Thu (weekday 6,0,1,2,3) 08:00–16:30 starts for 30/60 min slots ending by 17:00
    avail_rows = []
    for doc_id in range(1, 6):
        for wd in (6, 0, 1, 2, 3):
            avail_rows.append((doc_id, wd, "08:00", "17:00"))
    conn.executemany(
        """INSERT INTO doctor_availability (doctor_id, weekday, start_time, end_time)
           VALUES (?, ?, ?, ?)""",
        avail_rows,
    )

    ph, salt = auth_hash_fn("demo123")
    users_rows = [
        (ph, salt, UserRole.RECEPTIONIST.value, None, None),
    ]
    conn.execute(
        """INSERT INTO users (username, password_hash, salt, role, patient_id, doctor_id)
           VALUES ('reception', ?, ?, ?, ?, ?)""",
        users_rows[0],
    )
    for d in range(1, 6):
        h, s = auth_hash_fn("demo123")
        conn.execute(
            """INSERT INTO users (username, password_hash, salt, role, patient_id, doctor_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (f"doctor{d}", h, s, UserRole.DOCTOR.value, None, d),
        )
    for p in (1, 2, 3):
        h, s = auth_hash_fn("demo123")
        conn.execute(
            """INSERT INTO users (username, password_hash, salt, role, patient_id, doctor_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (f"patient{p}", h, s, UserRole.PATIENT.value, p, None),
        )

    # Persist dimension rows before appointments so foreign-key checks see committed parents
    # (avoids rare IntegrityError in long transactions on some SQLite builds).
    conn.commit()

    # 15 demo appointments: mix of past/future and statuses
    def iso(dt: datetime) -> str:
        return dt.replace(microsecond=0).isoformat()

    monday = now - timedelta(days=now.weekday())
    samples: List[Tuple] = [
        (1, 1, 1, iso(monday.replace(hour=9, minute=0)), 30, VisitReason.ROUTINE.value, AppointmentStatus.COMPLETED.value, "Routine check"),
        (2, 2, 2, iso(monday.replace(hour=10, minute=30)), 60, VisitReason.FOLLOW_UP.value, AppointmentStatus.COMPLETED.value, ""),
        (3, 3, 3, iso(monday.replace(hour=14, minute=0)), 30, VisitReason.ROUTINE.value, AppointmentStatus.CANCELLED.value, "Patient cancelled"),
        (4, 4, 4, iso((monday + timedelta(days=1)).replace(hour=11, minute=0)), 60, VisitReason.EMERGENCY.value, AppointmentStatus.COMPLETED.value, "Urgent"),
        (5, 5, 5, iso((monday + timedelta(days=2)).replace(hour=8, minute=30)), 30, VisitReason.ROUTINE.value, AppointmentStatus.LATE.value, ""),
        (6, 1, 2, iso((monday + timedelta(days=3)).replace(hour=15, minute=0)), 30, VisitReason.FOLLOW_UP.value, AppointmentStatus.CONFIRMED.value, ""),
        (7, 2, 3, iso((monday + timedelta(days=6)).replace(hour=9, minute=0)), 60, VisitReason.ROUTINE.value, AppointmentStatus.PENDING.value, "Needs confirmation"),
        (8, 1, 1, iso((monday + timedelta(days=7)).replace(hour=10, minute=0)), 30, VisitReason.ROUTINE.value, AppointmentStatus.CONFIRMED.value, ""),
        (9, 4, 4, iso((monday + timedelta(days=8)).replace(hour=13, minute=30)), 30, VisitReason.FOLLOW_UP.value, AppointmentStatus.PENDING.value, ""),
        (10, 5, 5, iso((monday + timedelta(days=9)).replace(hour=16, minute=0)), 30, VisitReason.ROUTINE.value, AppointmentStatus.CONFIRMED.value, "Late slot"),
        (11, 2, 2, iso((monday + timedelta(days=10)).replace(hour=8, minute=0)), 60, VisitReason.ROUTINE.value, AppointmentStatus.CONFIRMED.value, ""),
        (12, 3, 3, iso((monday + timedelta(days=15)).replace(hour=11, minute=30)), 30, VisitReason.EMERGENCY.value, AppointmentStatus.PENDING.value, ""),
        (13, 1, 1, iso((monday + timedelta(days=16)).replace(hour=14, minute=30)), 30, VisitReason.FOLLOW_UP.value, AppointmentStatus.CANCELLED.value, ""),
        (14, 4, 4, iso((monday + timedelta(days=13)).replace(hour=9, minute=30)), 60, VisitReason.ROUTINE.value, AppointmentStatus.CONFIRMED.value, ""),
        (15, 5, 5, iso((monday + timedelta(days=17)).replace(hour=15, minute=30)), 30, VisitReason.ROUTINE.value, AppointmentStatus.PENDING.value, ""),
    ]
    for row in samples:
        pid, did, cid, start_at, dur, reason, status, notes = row
        ts = iso(now)
        conn.execute(
            """INSERT INTO appointments
            (patient_id, doctor_id, clinic_id, start_at, duration_minutes, reason, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, did, cid, start_at, dur, reason, status, notes, ts, ts),
        )

    _set_meta(conn, "seed_version", str(SEED_VERSION))
    conn.commit()


# --- Users / auth lookups ---


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username.strip(),),
    ).fetchone()


def list_clinics(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM clinics ORDER BY name"))


def list_doctors(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT d.*, c.name AS clinic_name FROM doctors d
               JOIN clinics c ON c.id = d.clinic_id ORDER BY d.name"""
        )
    )


def list_patients(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM patients ORDER BY name"))


def get_patient(conn: sqlite3.Connection, patient_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()


def insert_patient(conn: sqlite3.Connection, name: str, phone: str, email: str) -> int:
    ts = datetime.now().replace(microsecond=0).isoformat()
    cur = conn.execute(
        "INSERT INTO patients (name, phone, email, created_at) VALUES (?, ?, ?, ?)",
        (name.strip(), phone.strip(), email.strip(), ts),
    )
    return int(cur.lastrowid)


def update_patient(conn: sqlite3.Connection, patient_id: int, name: str, phone: str, email: str) -> None:
    """Update patient contact fields (does not change id or created_at)."""
    conn.execute(
        "UPDATE patients SET name = ?, phone = ?, email = ? WHERE id = ?",
        (name.strip(), phone.strip(), email.strip(), int(patient_id)),
    )


def list_doctor_availability(conn: sqlite3.Connection, doctor_id: Optional[int] = None) -> List[sqlite3.Row]:
    if doctor_id is None:
        return list(conn.execute("SELECT * FROM doctor_availability ORDER BY doctor_id, weekday"))
    return list(
        conn.execute(
            "SELECT * FROM doctor_availability WHERE doctor_id = ? ORDER BY weekday",
            (doctor_id,),
        )
    )


def insert_availability(
    conn: sqlite3.Connection,
    doctor_id: int,
    weekday: int,
    start_time: str,
    end_time: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO doctor_availability (doctor_id, weekday, start_time, end_time)
           VALUES (?, ?, ?, ?)""",
        (doctor_id, weekday, start_time, end_time),
    )
    return int(cur.lastrowid)


def delete_availability(conn: sqlite3.Connection, avail_id: int) -> None:
    conn.execute("DELETE FROM doctor_availability WHERE id = ?", (avail_id,))


def fetch_appointments_joined(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT a.*,
                   p.name AS patient_name, p.phone AS patient_phone, p.email AS patient_email,
                   d.name AS doctor_name,
                   c.name AS clinic_name
            FROM appointments a
            JOIN patients p ON p.id = a.patient_id
            JOIN doctors d ON d.id = a.doctor_id
            JOIN clinics c ON c.id = a.clinic_id
            ORDER BY a.start_at
            """
        )
    )


def fetch_appointment(conn: sqlite3.Connection, appt_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.*,
               p.name AS patient_name, p.phone AS patient_phone, p.email AS patient_email,
               d.name AS doctor_name,
               c.name AS clinic_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        JOIN clinics c ON c.id = a.clinic_id
        WHERE a.id = ?
        """,
        (appt_id,),
    ).fetchone()


def fetch_patient_appointments(conn: sqlite3.Connection, patient_id: int) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT a.*,
                   d.name AS doctor_name,
                   c.name AS clinic_name
            FROM appointments a
            JOIN doctors d ON d.id = a.doctor_id
            JOIN clinics c ON c.id = a.clinic_id
            WHERE a.patient_id = ?
            ORDER BY a.start_at DESC
            """,
            (patient_id,),
        )
    )


def fetch_doctor_appointments_for_date(
    conn: sqlite3.Connection, doctor_id: int, day: datetime
) -> List[sqlite3.Row]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return list(
        conn.execute(
            """
            SELECT a.*,
                   p.name AS patient_name, p.phone AS patient_phone
            FROM appointments a
            JOIN patients p ON p.id = a.patient_id
            WHERE a.doctor_id = ? AND a.start_at >= ? AND a.start_at < ?
            ORDER BY a.start_at
            """,
            (doctor_id, start.isoformat(), end.isoformat()),
        )
    )


def count_overlapping_appointments(
    conn: sqlite3.Connection,
    doctor_id: int,
    start_at: datetime,
    duration_minutes: int,
    exclude_id: Optional[int] = None,
) -> int:
    new_start = start_at
    new_end = start_at + timedelta(minutes=duration_minutes)
    ns = new_start.isoformat()
    ne = new_end.isoformat()
    if exclude_id is None:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM appointments
            WHERE doctor_id = ?
              AND status != ?
              AND start_at < ?
              AND datetime(start_at, '+' || duration_minutes || ' minutes') > ?
            """,
            (doctor_id, AppointmentStatus.CANCELLED.value, ne, ns),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM appointments
            WHERE doctor_id = ?
              AND id != ?
              AND status != ?
              AND start_at < ?
              AND datetime(start_at, '+' || duration_minutes || ' minutes') > ?
            """,
            (doctor_id, exclude_id, AppointmentStatus.CANCELLED.value, ne, ns),
        ).fetchone()
    return int(row["c"])


def insert_appointment(
    conn: sqlite3.Connection,
    patient_id: int,
    doctor_id: int,
    clinic_id: int,
    start_at: datetime,
    duration_minutes: int,
    reason: str,
    status: str,
    notes: Optional[str],
) -> int:
    ts = datetime.now().replace(microsecond=0).isoformat()
    cur = conn.execute(
        """
        INSERT INTO appointments
        (patient_id, doctor_id, clinic_id, start_at, duration_minutes, reason, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            doctor_id,
            clinic_id,
            start_at.replace(microsecond=0).isoformat(),
            duration_minutes,
            reason,
            status,
            notes,
            ts,
            ts,
        ),
    )
    return int(cur.lastrowid)


def update_appointment(
    conn: sqlite3.Connection,
    appt_id: int,
    patient_id: int,
    doctor_id: int,
    clinic_id: int,
    start_at: datetime,
    duration_minutes: int,
    reason: str,
    status: str,
    notes: Optional[str],
) -> None:
    ts = datetime.now().replace(microsecond=0).isoformat()
    conn.execute(
        """
        UPDATE appointments SET
            patient_id = ?, doctor_id = ?, clinic_id = ?, start_at = ?, duration_minutes = ?,
            reason = ?, status = ?, notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            patient_id,
            doctor_id,
            clinic_id,
            start_at.replace(microsecond=0).isoformat(),
            duration_minutes,
            reason,
            status,
            notes,
            ts,
            appt_id,
        ),
    )


def update_appointment_status(conn: sqlite3.Connection, appt_id: int, status: str) -> None:
    ts = datetime.now().replace(microsecond=0).isoformat()
    conn.execute(
        "UPDATE appointments SET status = ?, updated_at = ? WHERE id = ?",
        (status, ts, appt_id),
    )


def delete_appointment(conn: sqlite3.Connection, appt_id: int) -> None:
    conn.execute("DELETE FROM appointments WHERE id = ?", (appt_id,))


def fetch_doctor_bookings_on_date(
    conn: sqlite3.Connection,
    doctor_id: int,
    day: date,
) -> List[sqlite3.Row]:
    """Non-cancelled appointments for overlap checks (cancelled slots treated as free)."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    return list(
        conn.execute(
            """
            SELECT start_at, duration_minutes, status
            FROM appointments
            WHERE doctor_id = ?
              AND start_at >= ? AND start_at < ?
              AND status != ?
            """,
            (
                doctor_id,
                start.isoformat(),
                end.isoformat(),
                AppointmentStatus.CANCELLED.value,
            ),
        )
    )
