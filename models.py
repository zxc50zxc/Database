"""
Data models, enums, and constants for the appointment system.
No database I/O in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    PATIENT = "patient"
    RECEPTIONIST = "receptionist"
    DOCTOR = "doctor"


class AppointmentStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    LATE = "late"


class VisitReason(str, Enum):
    ROUTINE = "routine"
    FOLLOW_UP = "follow_up"
    EMERGENCY = "emergency"


# Clinic policy: Python weekday Monday=0 ... Sunday=6
CLOSED_WEEKDAYS = {4, 5}  # Friday, Saturday
WORK_START_HOUR = 8
WORK_END_HOUR = 17  # 17:00 exclusive end for new slot end (last start must end by 17:00)
SAME_DAY_BOOKING_CUTOFF_HOUR = 16  # No same-day booking after this hour
MIN_CANCEL_HOURS_BEFORE = 24
ALLOWED_DURATIONS_MINUTES = (30, 60)

SEED_VERSION = 1


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    role: UserRole
    patient_id: Optional[int]
    doctor_id: Optional[int]


@dataclass(frozen=True)
class ClinicRecord:
    id: int
    name: str
    address: Optional[str]


@dataclass(frozen=True)
class DoctorRecord:
    id: int
    name: str
    clinic_id: int
    specialty: Optional[str]


@dataclass(frozen=True)
class PatientRecord:
    id: int
    name: str
    phone: str
    email: str


@dataclass(frozen=True)
class AppointmentRecord:
    id: int
    patient_id: int
    doctor_id: int
    clinic_id: int
    start_at: str  # ISO format
    duration_minutes: int
    reason: str
    status: str
    notes: Optional[str]
    created_at: str
    updated_at: str
