"""
Streamlit entrypoint: bilingual UI (ar/en), RTL/LTR theme, role dashboards.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

import pandas as pd
import streamlit as st

import auth
import database as db
from i18n import status_label, t, ui_lang
from models import AppointmentStatus, UserRole, VisitReason
from utils import (
    ValidationResult,
    appointments_dataframe,
    generate_candidate_slots,
    parse_iso_datetime,
    report_busiest_doctors,
    report_cancellation_rate,
    report_daily_counts,
    report_top_reasons,
    report_upcoming_within_hours,
    validate_new_appointment,
    validate_reschedule_or_cancel,
)


def inject_ui_theme() -> None:
    """RTL for Arabic, LTR for English. Streamlit widgets follow document direction."""
    lang = ui_lang()
    if lang == "ar":
        st.markdown(
            """
            <style>
            @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Arabic:wght@400;600;700&display=swap');
            html, body, [class*="css"]  {
                direction: rtl;
                font-family: 'Noto Sans Arabic', 'Segoe UI', Tahoma, sans-serif;
            }
            .main .block-container { padding-top: 1.2rem; max-width: 1200px; }
            div[data-testid="stSidebar"] { direction: rtl; text-align: right; }
            h1, h2, h3 { color: #0d47a1; }
            .stButton>button {
                background: linear-gradient(90deg, #1565c0, #2e7d32);
                color: white;
                border-radius: 8px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <style>
            html, body, [class*="css"]  {
                direction: ltr;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            .main .block-container { padding-top: 1.2rem; max-width: 1200px; }
            div[data-testid="stSidebar"] { direction: ltr; text-align: left; }
            h1, h2, h3 { color: #0d47a1; }
            .stButton>button {
                background: linear-gradient(90deg, #1565c0, #2e7d32);
                color: white;
                border-radius: 8px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )


@st.cache_resource
def get_connection():
    """Initialize schema, seed demo data once, and reuse the SQLite connection."""
    conn = db.get_connection()
    db.init_db(conn)
    db.seed_demo_data(conn, auth.hash_password)
    return conn


def show_error_msg(primary: str, detail_en: Optional[str] = None) -> None:
    st.error(primary)
    if detail_en:
        with st.expander(t("details")):
            st.code(detail_en)


def show_validation_error(v: ValidationResult) -> None:
    msg = v.message_en if ui_lang() == "en" else v.message_ar
    show_error_msg(msg, v.message_en if ui_lang() == "ar" else None)


def render_language_switch() -> None:
    st.caption(t("lang_label"))
    idx = 0 if ui_lang() == "ar" else 1
    choice = st.radio(
        "lang_radio",
        ("ar", "en"),
        index=idx,
        horizontal=True,
        format_func=lambda x: t("lang_ar") if x == "ar" else t("lang_en"),
        label_visibility="collapsed",
    )
    if choice != st.session_state.get("ui_lang", "ar"):
        st.session_state.ui_lang = choice
        st.rerun()


def render_login(conn) -> None:
    st.markdown(f"## {t('login_title')}")
    st.caption(t("login_caption"))
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input(t("username"))
        password = st.text_input(t("password"), type="password")
        submitted = st.form_submit_button(t("login_btn"))
    if submitted:
        try:
            user = auth.authenticate_user(conn, username, password)
        except Exception as exc:  # noqa: BLE001
            show_error_msg(t("err_login"), str(exc))
            return
        if user is None:
            show_error_msg(t("err_bad_creds"))
            return
        auth.login_user(user)
        st.rerun()


def rows_to_df(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def patient_dashboard(conn, user: dict) -> None:
    pid = user.get("patient_id")
    if pid is None:
        show_error_msg(t("not_patient"))
        return

    st.subheader(t("patient_board"))
    tab1, tab2, tab3 = st.tabs([t("tab_book"), t("tab_mine"), t("tab_edit")])

    doctors = db.list_doctors(conn)
    clinics = db.list_clinics(conn)
    doc_df = rows_to_df(doctors)

    reason_opts = [VisitReason.ROUTINE.value, VisitReason.FOLLOW_UP.value, VisitReason.EMERGENCY.value]

    def reason_fmt(code: str) -> str:
        return {
            VisitReason.ROUTINE.value: t("reason_routine"),
            VisitReason.FOLLOW_UP.value: t("reason_follow"),
            VisitReason.EMERGENCY.value: t("reason_emergency"),
        }[code]

    with tab1:
        st.markdown(f"#### {t('book_new')}")
        c1, c2, c3 = st.columns(3)
        with c1:
            clinic_names = {r["name"]: r["id"] for r in clinics}
            clinic_pick = st.selectbox(t("clinic"), list(clinic_names.keys()))
            clinic_id = clinic_names[clinic_pick]
        filtered_docs = doc_df[doc_df["clinic_id"] == clinic_id] if not doc_df.empty else doc_df
        with c2:
            if filtered_docs.empty:
                st.warning(t("no_doctors_clinic"))
                doc_id = None
            else:
                doc_labels = filtered_docs.apply(lambda r: f"{r['name']} (#{r['id']})", axis=1).tolist()
                doc_ids = filtered_docs["id"].tolist()
                idx = st.selectbox(t("doctor"), range(len(doc_ids)), format_func=lambda i: doc_labels[i])
                doc_id = int(doc_ids[idx])
        with c3:
            min_d = date.today()
            picked = st.date_input(t("date"), min_value=min_d)

        reason = st.selectbox(t("visit_reason"), reason_opts, format_func=reason_fmt)
        duration = st.radio(t("duration"), [30, 60], horizontal=True)
        notes = st.text_area(t("notes"), "")

        slots: List = []
        if doc_id is not None:
            avail = db.list_doctor_availability(conn, doc_id)
            booked_rows = db.fetch_doctor_bookings_on_date(conn, doc_id, picked)
            booked = [(parse_iso_datetime(r["start_at"]), int(r["duration_minutes"])) for r in booked_rows]
            slots = generate_candidate_slots(picked, avail, booked)
            slots = [(s, d) for s, d in slots if d == duration]

        if doc_id is None:
            pass
        elif not slots:
            st.info(t("no_slots"))
        else:
            suf = t("min_suffix")
            labels = [f"{s.strftime('%Y-%m-%d %H:%M')} — {d} {suf}" for s, d in slots]
            choice = st.selectbox(t("pick_time"), range(len(labels)), format_func=lambda i: labels[i])
            if st.button(t("confirm_book"), type="primary"):
                start, dur = slots[choice]
                v = validate_new_appointment(start, dur)
                if not v.ok:
                    show_validation_error(v)
                elif db.count_overlapping_appointments(conn, doc_id, start, dur) > 0:
                    show_error_msg(t("slot_overlap"), "Doctor double-booking")
                else:
                    try:
                        db.insert_appointment(
                            conn,
                            pid,
                            doc_id,
                            clinic_id,
                            start,
                            dur,
                            reason,
                            AppointmentStatus.CONFIRMED.value,
                            notes or None,
                        )
                        conn.commit()
                        st.success(t("success_booked"))
                    except Exception as exc:  # noqa: BLE001
                        show_error_msg(t("fail_save"), str(exc))

    with tab2:
        st.markdown(f"#### {t('my_history')}")
        rows = db.fetch_patient_appointments(conn, pid)
        df = rows_to_df(rows)
        if df.empty:
            st.info(t("no_appts"))
        else:
            q = st.text_input(t("search_table"), "")
            view = df
            if q:
                mask = view.astype(str).apply(lambda c: c.str.contains(q, case=False)).any(axis=1)
                view = view[mask]
            st.dataframe(view, use_container_width=True, hide_index=True)

    with tab3:
        st.markdown(f"#### {t('edit_cancel')}")
        rows = db.fetch_patient_appointments(conn, pid)
        df = rows_to_df(rows)
        if df.empty:
            st.info(t("no_appts"))
            return
        ids = df["id"].tolist()
        labels = [f"#{i} — {r['start_at']} — {r['doctor_name']}" for i, r in zip(ids, df.to_dict("records"))]
        idx = st.selectbox(t("pick_appt"), range(len(ids)), format_func=lambda i: labels[i])
        appt_id = int(ids[idx])
        row = db.fetch_appointment(conn, appt_id)
        if row is None:
            return
        start_at = parse_iso_datetime(row["start_at"])
        st.write(f"{t('current_status')}: **{status_label(str(row['status']))}**")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(t("cancel_appt")):
                vr = validate_reschedule_or_cancel(start_at)
                if not vr.ok:
                    show_validation_error(vr)
                else:
                    try:
                        db.update_appointment_status(conn, appt_id, AppointmentStatus.CANCELLED.value)
                        conn.commit()
                        st.success(t("success_cancelled"))
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error_msg(t("fail_cancel"), str(exc))
        with col_b:
            st.caption(t("edit_hint"))

        new_date = st.date_input(t("new_date"), value=start_at.date())
        new_time = st.time_input(t("new_time"), value=start_at.time())
        if st.button(t("save_edit")):
            new_start = datetime.combine(new_date, new_time)
            vr = validate_reschedule_or_cancel(start_at)
            if not vr.ok:
                show_validation_error(vr)
            else:
                v2 = validate_new_appointment(new_start, int(row["duration_minutes"]))
                if not v2.ok:
                    show_validation_error(v2)
                elif (
                    db.count_overlapping_appointments(
                        conn,
                        int(row["doctor_id"]),
                        new_start,
                        int(row["duration_minutes"]),
                        exclude_id=appt_id,
                    )
                    > 0
                ):
                    show_error_msg(t("overlap_other"), "overlap")
                else:
                    try:
                        db.update_appointment(
                            conn,
                            appt_id,
                            int(row["patient_id"]),
                            int(row["doctor_id"]),
                            int(row["clinic_id"]),
                            new_start,
                            int(row["duration_minutes"]),
                            row["reason"],
                            row["status"],
                            row["notes"],
                        )
                        conn.commit()
                        st.success(t("success_edited"))
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error_msg(t("fail_edit"), str(exc))


def receptionist_dashboard(conn, user: dict) -> None:
    st.subheader(t("reception_board"))
    t1, t2, t3, t4 = st.tabs([t("tab_patients"), t("tab_appts"), t("tab_hours"), t("tab_reports")])

    with t1:
        st.markdown(f"#### {t('add_patient')}")
        with st.form("new_patient"):
            name = st.text_input(t("name"))
            phone = st.text_input(t("phone"))
            email = st.text_input(t("email"))
            if st.form_submit_button(t("save_patient")):
                try:
                    new_id = db.insert_patient(conn, name, phone, email)
                    conn.commit()
                    st.success(f"{t('patient_created')} {new_id}")
                except Exception as exc:  # noqa: BLE001
                    show_error_msg(t("fail_patient"), str(exc))
        st.markdown(f"#### {t('patient_list')}")
        pts = rows_to_df(db.list_patients(conn))
        q = st.text_input(t("search_patient"), key="pq")
        if not pts.empty and q:
            m = pts.astype(str).apply(lambda c: c.str.contains(q, case=False)).any(axis=1)
            pts = pts[m]
        st.dataframe(pts, use_container_width=True, hide_index=True)

    with t2:
        st.markdown(f"#### {t('manage_appts')}")
        all_rows = db.fetch_appointments_joined(conn)
        df = rows_to_df(all_rows)
        if df.empty:
            st.info(t("no_appts"))
        else:
            df["start_at"] = pd.to_datetime(df["start_at"])
            c1, c2, c3 = st.columns(3)
            with c1:
                d_from = st.date_input(t("from_date"), value=date.today() - timedelta(days=7))
            with c2:
                d_to = st.date_input(t("to_date"), value=date.today() + timedelta(days=30))
            with c3:
                st.write("")
                st.caption(t("filter_hint"))
            doc_names = sorted(df["doctor_name"].dropna().unique().tolist())
            clin_names = sorted(df["clinic_name"].dropna().unique().tolist())
            doc_f = st.multiselect(t("doctor"), doc_names, default=doc_names)
            cl_f = st.multiselect(t("clinic"), clin_names, default=clin_names)
            view = df[(df["start_at"].dt.date >= d_from) & (df["start_at"].dt.date <= d_to)]
            if doc_f:
                view = view[view["doctor_name"].isin(doc_f)]
            if cl_f:
                view = view[view["clinic_name"].isin(cl_f)]
            st.dataframe(view, use_container_width=True, hide_index=True)

        st.markdown(f"##### {t('create_edit_appt')}")
        doctors = db.list_doctors(conn)
        patients = db.list_patients(conn)
        if not doctors or not patients:
            st.warning(t("need_data"))
        else:
            mode = st.radio(
                t("mode"),
                ("new", "edit"),
                horizontal=True,
                format_func=lambda x: t("mode_new") if x == "new" else t("mode_edit"),
            )
            appt_id = None
            existing = None
            if mode == "edit":
                if df.empty:
                    st.warning(t("no_appts_edit"))
                else:
                    ids = df["id"].tolist()
                    pick = st.selectbox(t("pick_appt"), ids)
                    appt_id = int(pick)
                    existing = db.fetch_appointment(conn, appt_id)

            if mode == "new" or (mode == "edit" and existing is not None):
                doc_map = {f"{r['name']} (#{r['id']})": r["id"] for r in doctors}
                pat_map = {f"{r['name']} (#{r['id']})": r["id"] for r in patients}
                dlab = st.selectbox(t("doctor"), list(doc_map.keys()))
                plab = st.selectbox(t("patient"), list(pat_map.keys()))
                doc_id = doc_map[dlab]
                pat_id = pat_map[plab]
                clinic_id = int(next(r["clinic_id"] for r in doctors if r["id"] == doc_id))
                default_day = (
                    parse_iso_datetime(existing["start_at"]).date()
                    if existing
                    else date.today()
                )
                default_t = (
                    parse_iso_datetime(existing["start_at"]).time()
                    if existing
                    else datetime.strptime("09:00", "%H:%M").time()
                )
                day = st.date_input(t("date"), value=default_day)
                tm = st.time_input(t("time"), value=default_t)
                dur_vals = [30, 60]
                dur_idx = 0
                if existing and int(existing["duration_minutes"]) in dur_vals:
                    dur_idx = dur_vals.index(int(existing["duration_minutes"]))
                duration = st.selectbox(t("duration_min"), dur_vals, index=dur_idx)
                reason_opts = [
                    VisitReason.ROUTINE.value,
                    VisitReason.FOLLOW_UP.value,
                    VisitReason.EMERGENCY.value,
                ]
                reason_labels = {
                    VisitReason.ROUTINE.value: t("reason_routine"),
                    VisitReason.FOLLOW_UP.value: t("reason_follow"),
                    VisitReason.EMERGENCY.value: t("reason_emergency_short"),
                }
                r_idx = 0
                if existing and existing["reason"] in reason_opts:
                    r_idx = reason_opts.index(existing["reason"])
                reason = st.selectbox(
                    t("reason"),
                    reason_opts,
                    index=r_idx,
                    format_func=lambda x: reason_labels[x],
                )
                status_opts = [
                    AppointmentStatus.CONFIRMED.value,
                    AppointmentStatus.PENDING.value,
                    AppointmentStatus.CANCELLED.value,
                    AppointmentStatus.COMPLETED.value,
                    AppointmentStatus.LATE.value,
                ]
                s_idx = 0
                if existing and existing["status"] in status_opts:
                    s_idx = status_opts.index(existing["status"])
                status = st.selectbox(
                    t("status"),
                    status_opts,
                    index=s_idx,
                    format_func=status_label,
                )
                notes = st.text_area(t("notes"), value=existing["notes"] if existing else "")

                start = datetime.combine(day, tm)
                if st.button(t("save_appt")):
                    is_new = mode == "new"
                    old_start = parse_iso_datetime(existing["start_at"]) if existing else None
                    if is_new:
                        v = validate_new_appointment(start, int(duration))
                        if not v.ok:
                            show_validation_error(v)
                        elif db.count_overlapping_appointments(conn, doc_id, start, int(duration)) > 0:
                            show_error_msg(t("overlap_dr"), "overlap")
                        else:
                            try:
                                db.insert_appointment(
                                    conn,
                                    pat_id,
                                    doc_id,
                                    clinic_id,
                                    start,
                                    int(duration),
                                    reason,
                                    status,
                                    notes or None,
                                )
                                conn.commit()
                                st.success(t("success_saved"))
                                st.rerun()
                            except Exception as exc:  # noqa: BLE001
                                show_error_msg(t("fail_save"), str(exc))
                    else:
                        time_changed = old_start is not None and start != old_start
                        if time_changed:
                            v = validate_new_appointment(start, int(duration))
                            if not v.ok:
                                show_validation_error(v)
                            else:
                                vr = validate_reschedule_or_cancel(old_start)
                                if not vr.ok:
                                    show_validation_error(vr)
                                elif (
                                    db.count_overlapping_appointments(
                                        conn, doc_id, start, int(duration), exclude_id=appt_id
                                    )
                                    > 0
                                ):
                                    show_error_msg(t("overlap_dr"), "overlap")
                                else:
                                    try:
                                        db.update_appointment(
                                            conn,
                                            appt_id,
                                            pat_id,
                                            doc_id,
                                            clinic_id,
                                            start,
                                            int(duration),
                                            reason,
                                            status,
                                            notes or None,
                                        )
                                        conn.commit()
                                        st.success(t("success_saved"))
                                        st.rerun()
                                    except Exception as exc:  # noqa: BLE001
                                        show_error_msg(t("fail_save"), str(exc))
                        else:
                            if (
                                db.count_overlapping_appointments(
                                    conn, doc_id, start, int(duration), exclude_id=appt_id
                                )
                                > 0
                            ):
                                show_error_msg(t("overlap_dr"), "overlap")
                            else:
                                try:
                                    db.update_appointment(
                                        conn,
                                        appt_id,
                                        pat_id,
                                        doc_id,
                                        clinic_id,
                                        start,
                                        int(duration),
                                        reason,
                                        status,
                                        notes or None,
                                    )
                                    conn.commit()
                                    st.success(t("success_saved"))
                                    st.rerun()
                                except Exception as exc:  # noqa: BLE001
                                    show_error_msg(t("fail_save"), str(exc))

                if mode == "edit" and appt_id:
                    if st.button(t("delete_appt")):
                        try:
                            db.delete_appointment(conn, appt_id)
                            conn.commit()
                            st.success(t("success_deleted"))
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error_msg(t("fail_delete"), str(exc))

    with t3:
        st.markdown(f"#### {t('weekly_hours')}")
        docs = db.list_doctors(conn)
        if not docs:
            st.info(t("no_doctors_list"))
        else:
            doc_labels = [f"{r['name']} (#{r['id']})" for r in docs]
            d_idx = st.selectbox(
                t("doctor"),
                range(len(docs)),
                format_func=lambda i: doc_labels[i],
            )
            doc_id = int(dict(docs[d_idx])["id"])
            st.write(t("weekday_help"))
            rows = db.list_doctor_availability(conn, doc_id)
            st.dataframe(rows_to_df(rows), use_container_width=True, hide_index=True)
            wd = st.number_input(t("weekday_num"), min_value=0, max_value=6, value=6)
            st1, st2 = st.columns(2)
            with st1:
                s_t = st.text_input(t("from_hhmm"), value="08:00")
            with st2:
                e_t = st.text_input(t("to_hhmm"), value="17:00")
            if st.button(t("add_slot")):
                try:
                    db.insert_availability(conn, doc_id, int(wd), s_t, e_t)
                    conn.commit()
                    st.success(t("success_slot_add"))
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error_msg(t("fail_slot_add"), str(exc))
            del_id = st.number_input(t("del_slot_id"), min_value=0, value=0)
            if del_id > 0 and st.button(t("del_slot")):
                try:
                    db.delete_availability(conn, int(del_id))
                    conn.commit()
                    st.success(t("success_slot_del"))
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error_msg(t("fail_slot_del"), str(exc))

    with t4:
        st.markdown(f"#### {t('reports')}")
        df = appointments_dataframe(conn)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric(t("metric_today"), report_daily_counts(df, today))
        with c2:
            st.metric(t("metric_cancel"), f"{report_cancellation_rate(df)*100:.1f}%")
        with c3:
            wk = 0
            if not df.empty:
                wk = int(
                    ((df["start_at"].dt.date >= week_start) & (df["start_at"].dt.date <= week_end)).sum()
                )
            st.metric(t("metric_week"), wk)
        st.markdown(f"##### {t('busiest')}")
        st.dataframe(report_busiest_doctors(df), use_container_width=True, hide_index=True)
        st.markdown(f"##### {t('top_reasons')}")
        st.dataframe(report_top_reasons(df), use_container_width=True, hide_index=True)
        st.markdown(f"##### {t('alerts_24h')}")
        up = report_upcoming_within_hours(df, 24)
        st.dataframe(up, use_container_width=True, hide_index=True)

        st.markdown(f"#### {t('csv_export')}")
        table = st.selectbox(t("table"), ["appointments", "patients", "doctors", "clinics"])
        try:
            data = conn.execute(f"SELECT * FROM {table}").fetchall()
            pdf = pd.DataFrame([dict(r) for r in data])
            csv_bytes = pdf.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                t("download_csv"),
                data=csv_bytes,
                file_name=f"{table}.csv",
                mime="text/csv",
                key=f"csv_dl_{table}",
            )
        except Exception as exc:  # noqa: BLE001
            show_error_msg(t("fail_export"), str(exc))


def doctor_dashboard(conn, user: dict) -> None:
    did = user.get("doctor_id")
    if did is None:
        show_error_msg(t("not_doctor"))
        return
    st.subheader(t("doctor_board"))
    day = st.date_input(t("day"), value=date.today())
    rows = db.fetch_doctor_appointments_for_date(conn, did, datetime.combine(day, time.min))
    df = rows_to_df(rows)
    if df.empty:
        st.info(t("no_appts_day"))
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        appt_id = st.selectbox(t("pick_appt_status"), df["id"].tolist())
        status_opts = [
            AppointmentStatus.COMPLETED.value,
            AppointmentStatus.LATE.value,
            AppointmentStatus.CANCELLED.value,
            AppointmentStatus.CONFIRMED.value,
            AppointmentStatus.PENDING.value,
        ]
        new_status = st.selectbox(t("status"), status_opts, format_func=status_label)
        if st.button(t("update_status")):
            try:
                db.update_appointment_status(conn, int(appt_id), new_status)
                conn.commit()
                st.success(t("success_updated"))
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                show_error_msg(t("fail_update"), str(exc))


def main() -> None:
    if "ui_lang" not in st.session_state:
        st.session_state.ui_lang = "ar"

    st.set_page_config(page_title=t("page_title"), page_icon="🏥", layout="wide")
    inject_ui_theme()
    conn = get_connection()
    auth.init_session_state()
    user = auth.current_user()

    with st.sidebar:
        st.markdown(f"### 🏥 {t('app_name')}")
        render_language_switch()
        st.divider()
        if user:
            st.write(f"**{user['username']}**")
            st.caption(str(user["role"].value))
            if st.button(t("logout")):
                auth.logout_user()
                st.rerun()
        with st.expander(t("demo_logins")):
            st.markdown(
                """
- `reception` / `demo123` — receptionist  
- `doctor1` … `doctor5` / `demo123`  
- `patient1` … `patient3` / `demo123`  
"""
            )

    if user is None:
        render_login(conn)
        return

    role: UserRole = user["role"]
    if role == UserRole.PATIENT:
        patient_dashboard(conn, user)
    elif role == UserRole.RECEPTIONIST:
        receptionist_dashboard(conn, user)
    elif role == UserRole.DOCTOR:
        doctor_dashboard(conn, user)
    else:
        st.warning(t("unknown_role"))


if __name__ == "__main__":
    main()
