"""
Simple username/password authentication using PBKDF2 (stdlib).
Streamlit session helpers.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Callable, Optional, Tuple

import streamlit as st

from database import get_user_by_username
from models import UserRole


ITERATIONS = 390000
SESSION_KEYS = ("auth_user_id", "auth_username", "auth_role", "auth_patient_id", "auth_doctor_id")


def hash_password(password: str) -> Tuple[str, str]:
    """Return (password_hash_hex, salt_hex)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        ITERATIONS,
    )
    return dk.hex(), salt


def verify_password(password: str, password_hash_hex: str, salt_hex: str) -> bool:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_hex.encode("ascii"),
        ITERATIONS,
    )
    return secrets.compare_digest(dk.hex(), password_hash_hex)


def authenticate_user(conn, username: str, password: str) -> Optional[dict]:
    row = get_user_by_username(conn, username)
    if row is None:
        return None
    if not verify_password(password, row["password_hash"], row["salt"]):
        return None
    return {
        "user_id": int(row["id"]),
        "username": row["username"],
        "role": UserRole(row["role"]),
        "patient_id": int(row["patient_id"]) if row["patient_id"] is not None else None,
        "doctor_id": int(row["doctor_id"]) if row["doctor_id"] is not None else None,
    }


def init_session_state() -> None:
    for key in SESSION_KEYS:
        if key not in st.session_state:
            st.session_state[key] = None


def login_user(user: dict) -> None:
    st.session_state.auth_user_id = user["user_id"]
    st.session_state.auth_username = user["username"]
    st.session_state.auth_role = user["role"]
    st.session_state.auth_patient_id = user["patient_id"]
    st.session_state.auth_doctor_id = user["doctor_id"]


def logout_user() -> None:
    for key in SESSION_KEYS:
        st.session_state[key] = None


def current_user() -> Optional[dict]:
    if st.session_state.get("auth_user_id") is None:
        return None
    return {
        "user_id": st.session_state.auth_user_id,
        "username": st.session_state.auth_username,
        "role": st.session_state.auth_role,
        "patient_id": st.session_state.auth_patient_id,
        "doctor_id": st.session_state.auth_doctor_id,
    }


def require_login(render_login: Callable[[], None]) -> Optional[dict]:
    init_session_state()
    user = current_user()
    if user is None:
        render_login()
        return None
    return user


def hash_password_factory() -> Callable[[str], Tuple[str, str]]:
    """For database.seed_demo_data injection without circular imports at module level."""
    return hash_password
