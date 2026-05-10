"""
Validation helpers, clinic policy, reporting (pandas), and CSV export.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from models import (
    ALLOWED_DURATIONS_MINUTES,
    CLOSED_WEEKDAYS,
    MIN_CANCEL_HOURS_BEFORE,
    SAME_DAY_BOOKING_CUTOFF_HOUR,
    WORK_END_HOUR,
    WORK_START_HOUR,
)


@dataclass
class ValidationResult:
    ok: bool
    message_en: str
    message_ar: str


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def combine_date_time(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def is_working_weekday(weekday: int) -> bool:
    """Python weekday: Monday=0 .. Sunday=6. Closed Friday=4, Saturday=5."""
    return weekday not in CLOSED_WEEKDAYS


def window_allows_slot(start: datetime, duration_minutes: int) -> bool:
    if not is_working_weekday(start.weekday()):
        return False
    day = start.date()
    open_at = datetime.combine(day, time(WORK_START_HOUR, 0))
    close_at = datetime.combine(day, time(WORK_END_HOUR, 0))
    end = start + timedelta(minutes=duration_minutes)
    return start >= open_at and end <= close_at


def same_day_booking_allowed(now: datetime, appointment_start: datetime) -> bool:
    if appointment_start.date() != now.date():
        return True
    return now.hour < SAME_DAY_BOOKING_CUTOFF_HOUR


def validate_new_appointment(
    start: datetime,
    duration_minutes: int,
    now: Optional[datetime] = None,
) -> ValidationResult:
    now = now or datetime.now()
    if duration_minutes not in ALLOWED_DURATIONS_MINUTES:
        return ValidationResult(
            False,
            "Duration must be 30 or 60 minutes.",
            "مدة الموعد يجب أن تكون 30 أو 60 دقيقة.",
        )
    if start <= now:
        return ValidationResult(
            False,
            "Cannot book an appointment in the past.",
            "لا يمكن حجز موعد في الماضي.",
        )
    if not is_working_weekday(start.weekday()):
        return ValidationResult(
            False,
            "Clinic is closed on this weekday (Fri/Sat).",
            "العيادة مغلقة في هذا اليوم (الجمعة والسبت).",
        )
    if not window_allows_slot(start, duration_minutes):
        return ValidationResult(
            False,
            f"Appointment must fall within {WORK_START_HOUR:02d}:00–{WORK_END_HOUR:02d}:00.",
            f"يجب أن يكون الموعد ضمن أوقات العمل {WORK_START_HOUR:02d}:00–{WORK_END_HOUR:02d}:00.",
        )
    if not same_day_booking_allowed(now, start):
        return ValidationResult(
            False,
            f"Same-day booking is only allowed before {SAME_DAY_BOOKING_CUTOFF_HOUR:02d}:00.",
            f"الحجز لنفس اليوم متاح فقط قبل الساعة {SAME_DAY_BOOKING_CUTOFF_HOUR:02d}:00.",
        )
    return ValidationResult(True, "", "")


def validate_reschedule_or_cancel(
    appointment_start: datetime,
    now: Optional[datetime] = None,
) -> ValidationResult:
    now = now or datetime.now()
    if appointment_start - now < timedelta(hours=MIN_CANCEL_HOURS_BEFORE):
        return ValidationResult(
            False,
            f"Changes require at least {MIN_CANCEL_HOURS_BEFORE} hours notice.",
            f"التعديل أو الإلغاء يتطلب إشعاراً قبل {MIN_CANCEL_HOURS_BEFORE} ساعة على الأقل.",
        )
    return ValidationResult(True, "", "")


def generate_candidate_slots(
    day: date,
    doctor_availability_rows: List[sqlite3.Row],
    booked_starts_durations: List[Tuple[datetime, int]],
    slot_step_minutes: int = 30,
) -> List[Tuple[datetime, int]]:
    """
    Build available (start, duration) pairs for a doctor on a given day.
    Caller supplies booked intervals (typically non-cancelled appointments).
    """
    wd = day.weekday()
    pairs: List[Tuple[datetime, int]] = []

    def overlaps(cursor_start: datetime, dur: int) -> bool:
        end_slot = cursor_start + timedelta(minutes=dur)
        for bs, bd in booked_starts_durations:
            be = bs + timedelta(minutes=bd)
            if cursor_start < be and end_slot > bs:
                return True
        return False

    for row in doctor_availability_rows:
        if int(row["weekday"]) != wd:
            continue
        sh, sm = map(int, row["start_time"].split(":")[:2])
        eh, em = map(int, row["end_time"].split(":")[:2])
        cursor = datetime.combine(day, time(sh, sm))
        end_limit = datetime.combine(day, time(eh, em))

        while cursor < end_limit:
            for dur in ALLOWED_DURATIONS_MINUTES:
                end_slot = cursor + timedelta(minutes=dur)
                if end_slot > end_limit:
                    continue
                if not window_allows_slot(cursor, dur):
                    continue
                if not overlaps(cursor, dur):
                    pairs.append((cursor, dur))
            cursor += timedelta(minutes=slot_step_minutes)

    # Deduplicate identical (start, duration)
    seen: set[Tuple[str, int]] = set()
    out: List[Tuple[datetime, int]] = []
    for s, d in sorted(pairs, key=lambda x: (x[0], x[1])):
        key = (s.isoformat(), d)
        if key not in seen:
            seen.add(key)
            out.append((s, d))
    return out


# --- Reporting ---


def appointments_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT a.id, a.patient_id, a.doctor_id, a.clinic_id, a.start_at, a.duration_minutes,
               a.reason, a.status, a.notes, a.created_at, a.updated_at,
               p.name AS patient_name, d.name AS doctor_name, c.name AS clinic_name
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        JOIN doctors d ON d.id = a.doctor_id
        JOIN clinics c ON c.id = a.clinic_id
        """
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["start_at"] = pd.to_datetime(df["start_at"])
    return df


def report_daily_counts(df: pd.DataFrame, day: date) -> int:
    if df.empty:
        return 0
    mask = df["start_at"].dt.date == day
    return int(mask.sum())


def report_cancellation_rate(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    total = len(df)
    cancelled = (df["status"] == "cancelled").sum()
    return float(cancelled / total) if total else 0.0


def report_busiest_doctors(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["doctor_name", "appointments"])
    g = df.groupby("doctor_name").size().reset_index(name="appointments")
    return g.sort_values("appointments", ascending=False).head(top_n)


def report_top_reasons(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["reason", "count"])
    g = df.groupby("reason").size().reset_index(name="count")
    return g.sort_values("count", ascending=False).head(top_n)


def report_upcoming_within_hours(df: pd.DataFrame, hours: int = 24) -> pd.DataFrame:
    if df.empty:
        return df
    now = pd.Timestamp.now()
    end = now + pd.Timedelta(hours=hours)
    mask = (df["start_at"] >= now) & (df["start_at"] <= end) & (~df["status"].isin(["cancelled", "completed"]))
    return df.loc[mask].sort_values("start_at")


def export_query_to_csv(conn: sqlite3.Connection, sql: str, output_path: Path) -> Tuple[bool, str, str]:
    """
    Returns (success, message_ar, message_en).
    """
    try:
        df = pd.read_sql_query(sql, conn)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        return True, "تم تصدير الملف بنجاح.", "Export completed successfully."
    except Exception as exc:  # noqa: BLE001
        return False, "فشل التصدير. راجع التفاصيل.", f"Export failed: {exc}"


def export_table_to_csv(conn: sqlite3.Connection, table: str, output_path: Path) -> Tuple[bool, str, str]:
    allowed = {"appointments", "patients", "doctors", "clinics", "doctor_availability", "users"}
    if table not in allowed:
        return False, "جدول غير مسموح.", "Table not allowed for export."
    return export_query_to_csv(conn, f"SELECT * FROM {table}", output_path)
