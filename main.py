"""
Streamlit entrypoint: Arabic RTL UI, role-based dashboards.
Code and structure in English; user-facing strings in Arabic.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

import pandas as pd
import streamlit as st

import auth
import database as db
from models import AppointmentStatus, UserRole, VisitReason
from utils import (
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


def inject_rtl_theme() -> None:
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


@st.cache_resource
def get_connection():
    """Initialize schema, seed demo data once, and reuse the SQLite connection."""
    conn = db.get_connection()
    db.init_db(conn)
    db.seed_demo_data(conn, auth.hash_password)
    return conn


def show_error_ar(msg_ar: str, detail_en: Optional[str] = None) -> None:
    st.error(msg_ar)
    if detail_en:
        with st.expander("Details / تفاصيل تقنية"):
            st.code(detail_en)


def render_login(conn) -> None:
    st.markdown("## تسجيل الدخول")
    st.caption("نظام جدولة المواعيد — مستشفى / عيادة")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("اسم المستخدم")
        password = st.text_input("كلمة المرور", type="password")
        submitted = st.form_submit_button("دخول")
    if submitted:
        try:
            user = auth.authenticate_user(conn, username, password)
        except Exception as exc:  # noqa: BLE001
            show_error_ar("حدث خطأ أثناء تسجيل الدخول.", str(exc))
            return
        if user is None:
            show_error_ar("بيانات الدخول غير صحيحة.")
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
        show_error_ar("هذا الحساب غير مرتبط بملف مريض.")
        return

    st.subheader("لوحة المريض")
    tab1, tab2, tab3 = st.tabs(["حجز موعد", "مواعيدي", "تعديل / إلغاء"])

    doctors = db.list_doctors(conn)
    clinics = db.list_clinics(conn)
    doc_df = rows_to_df(doctors)

    with tab1:
        st.markdown("#### حجز موعد جديد")
        c1, c2, c3 = st.columns(3)
        with c1:
            clinic_names = {r["name"]: r["id"] for r in clinics}
            clinic_pick = st.selectbox("العيادة", list(clinic_names.keys()))
            clinic_id = clinic_names[clinic_pick]
        filtered_docs = doc_df[doc_df["clinic_id"] == clinic_id] if not doc_df.empty else doc_df
        with c2:
            if filtered_docs.empty:
                st.warning("لا يوجد أطباء لهذه العيادة.")
                doc_id = None
            else:
                doc_labels = filtered_docs.apply(lambda r: f"{r['name']} (#{r['id']})", axis=1).tolist()
                doc_ids = filtered_docs["id"].tolist()
                idx = st.selectbox("الطبيب", range(len(doc_ids)), format_func=lambda i: doc_labels[i])
                doc_id = int(doc_ids[idx])
        with c3:
            min_d = date.today()
            picked = st.date_input("التاريخ", min_value=min_d)

        reason = st.selectbox(
            "سبب الزيارة",
            [
                (VisitReason.ROUTINE.value, "فحص روتيني"),
                (VisitReason.FOLLOW_UP.value, "متابعة"),
                (VisitReason.EMERGENCY.value, "حالة طارئة"),
            ],
            format_func=lambda x: x[1],
        )[0]
        duration = st.radio("المدة", [30, 60], horizontal=True)
        notes = st.text_area("ملاحظات", "")

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
            st.info("لا توجد أوقات متاحة لهذا اليوم والمدة المختارة.")
        else:
            labels = [f"{s.strftime('%Y-%m-%d %H:%M')} — {d} دقيقة" for s, d in slots]
            choice = st.selectbox("اختر الوقت", range(len(labels)), format_func=lambda i: labels[i])
            if st.button("تأكيد الحجز", type="primary"):
                start, dur = slots[choice]
                v = validate_new_appointment(start, dur)
                if not v.ok:
                    show_error_ar(v.message_ar, v.message_en)
                elif db.count_overlapping_appointments(conn, doc_id, start, dur) > 0:
                    show_error_ar("هذا الوقت لم يعد متاحاً (تعارض).", "Doctor double-booking")
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
                        st.success("تم حجز الموعد بنجاح.")
                    except Exception as exc:  # noqa: BLE001
                        show_error_ar("فشل الحفظ.", str(exc))

    with tab2:
        st.markdown("#### سجل مواعيدي")
        rows = db.fetch_patient_appointments(conn, pid)
        df = rows_to_df(rows)
        if df.empty:
            st.info("لا توجد مواعيد.")
        else:
            q = st.text_input("بحث في الجدول", "")
            view = df
            if q:
                mask = view.astype(str).apply(lambda c: c.str.contains(q, case=False)).any(axis=1)
                view = view[mask]
            st.dataframe(view, use_container_width=True, hide_index=True)

    with tab3:
        st.markdown("#### تعديل أو إلغاء موعد")
        rows = db.fetch_patient_appointments(conn, pid)
        df = rows_to_df(rows)
        if df.empty:
            st.info("لا توجد مواعيد.")
            return
        ids = df["id"].tolist()
        labels = [f"#{i} — {r['start_at']} — {r['doctor_name']}" for i, r in zip(ids, df.to_dict("records"))]
        idx = st.selectbox("اختر الموعد", range(len(ids)), format_func=lambda i: labels[i])
        appt_id = int(ids[idx])
        row = db.fetch_appointment(conn, appt_id)
        if row is None:
            return
        start_at = parse_iso_datetime(row["start_at"])
        st.write(f"الحالة الحالية: **{row['status']}**")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("إلغاء الموعد"):
                vr = validate_reschedule_or_cancel(start_at)
                if not vr.ok:
                    show_error_ar(vr.message_ar, vr.message_en)
                else:
                    try:
                        db.update_appointment_status(conn, appt_id, AppointmentStatus.CANCELLED.value)
                        conn.commit()
                        st.success("تم إلغاء الموعد.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error_ar("فشل الإلغاء.", str(exc))
        with col_b:
            st.caption("التعديل: اختر تاريخ ووقت جديد ضمن نفس العيادة والطبيب")

        new_date = st.date_input("تاريخ جديد", value=start_at.date())
        new_time = st.time_input("وقت جديد", value=start_at.time())
        if st.button("حفظ التعديل"):
            new_start = datetime.combine(new_date, new_time)
            vr = validate_reschedule_or_cancel(start_at)
            if not vr.ok:
                show_error_ar(vr.message_ar, vr.message_en)
            else:
                v2 = validate_new_appointment(new_start, int(row["duration_minutes"]))
                if not v2.ok:
                    show_error_ar(v2.message_ar, v2.message_en)
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
                    show_error_ar("تعارض مع موعد آخر.", "overlap")
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
                        st.success("تم تعديل الموعد.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error_ar("فشل التعديل.", str(exc))


def receptionist_dashboard(conn, user: dict) -> None:
    st.subheader("لوحة موظف الاستقبال")
    t1, t2, t3, t4 = st.tabs(["المرضى", "المواعيد", "أوقات الأطباء", "تقارير وتصدير"])

    with t1:
        st.markdown("#### إضافة مريض")
        with st.form("new_patient"):
            name = st.text_input("الاسم")
            phone = st.text_input("الهاتف")
            email = st.text_input("البريد")
            if st.form_submit_button("حفظ المريض"):
                try:
                    new_id = db.insert_patient(conn, name, phone, email)
                    conn.commit()
                    st.success(f"تم إنشاء المريض رقم {new_id}")
                except Exception as exc:  # noqa: BLE001
                    show_error_ar("تعذر حفظ المريض.", str(exc))
        st.markdown("#### قائمة المرضى")
        pts = rows_to_df(db.list_patients(conn))
        q = st.text_input("بحث عن مريض", key="pq")
        if not pts.empty and q:
            m = pts.astype(str).apply(lambda c: c.str.contains(q, case=False)).any(axis=1)
            pts = pts[m]
        st.dataframe(pts, use_container_width=True, hide_index=True)

    with t2:
        st.markdown("#### إدارة المواعيد")
        all_rows = db.fetch_appointments_joined(conn)
        df = rows_to_df(all_rows)
        if df.empty:
            st.info("لا توجد مواعيد.")
        else:
            df["start_at"] = pd.to_datetime(df["start_at"])
            c1, c2, c3 = st.columns(3)
            with c1:
                d_from = st.date_input("من تاريخ", value=date.today() - timedelta(days=7))
            with c2:
                d_to = st.date_input("إلى تاريخ", value=date.today() + timedelta(days=30))
            with c3:
                st.write("")
                st.caption("فلترة حسب الطبيب / العيادة")
            doc_names = sorted(df["doctor_name"].dropna().unique().tolist())
            clin_names = sorted(df["clinic_name"].dropna().unique().tolist())
            doc_f = st.multiselect("الطبيب", doc_names, default=doc_names)
            cl_f = st.multiselect("العيادة", clin_names, default=clin_names)
            view = df[(df["start_at"].dt.date >= d_from) & (df["start_at"].dt.date <= d_to)]
            if doc_f:
                view = view[view["doctor_name"].isin(doc_f)]
            if cl_f:
                view = view[view["clinic_name"].isin(cl_f)]
            st.dataframe(view, use_container_width=True, hide_index=True)

        st.markdown("##### إنشاء أو تعديل موعد (للمريض)")
        doctors = db.list_doctors(conn)
        patients = db.list_patients(conn)
        if not doctors or not patients:
            st.warning("تحتاج بيانات أطباء ومرضى أولاً.")
        else:
            mode = st.radio("الوضع", ["جديد", "تعديل"], horizontal=True)
            appt_id = None
            existing = None
            if mode == "تعديل":
                if df.empty:
                    st.warning("لا يوجد مواعيد للتعديل.")
                else:
                    ids = df["id"].tolist()
                    pick = st.selectbox("اختر الموعد", ids)
                    appt_id = int(pick)
                    existing = db.fetch_appointment(conn, appt_id)

            if mode == "جديد" or (mode == "تعديل" and existing is not None):
                doc_map = {f"{r['name']} (#{r['id']})": r["id"] for r in doctors}
                pat_map = {f"{r['name']} (#{r['id']})": r["id"] for r in patients}
                dlab = st.selectbox("الطبيب", list(doc_map.keys()))
                plab = st.selectbox("المريض", list(pat_map.keys()))
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
                day = st.date_input("التاريخ", value=default_day)
                tm = st.time_input("الوقت", value=default_t)
                dur_vals = [30, 60]
                dur_idx = 0
                if existing and int(existing["duration_minutes"]) in dur_vals:
                    dur_idx = dur_vals.index(int(existing["duration_minutes"]))
                duration = st.selectbox("المدة (دقيقة)", dur_vals, index=dur_idx)
                reason_opts = [
                    VisitReason.ROUTINE.value,
                    VisitReason.FOLLOW_UP.value,
                    VisitReason.EMERGENCY.value,
                ]
                reason_labels = {
                    VisitReason.ROUTINE.value: "فحص روتيني",
                    VisitReason.FOLLOW_UP.value: "متابعة",
                    VisitReason.EMERGENCY.value: "طوارئ",
                }
                r_idx = 0
                if existing and existing["reason"] in reason_opts:
                    r_idx = reason_opts.index(existing["reason"])
                reason = st.selectbox(
                    "السبب",
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
                status = st.selectbox("الحالة", status_opts, index=s_idx)
                notes = st.text_area("ملاحظات", value=existing["notes"] if existing else "")

                start = datetime.combine(day, tm)
                if st.button("حفظ الموعد"):
                    is_new = mode == "جديد"
                    old_start = parse_iso_datetime(existing["start_at"]) if existing else None
                    if is_new:
                        v = validate_new_appointment(start, int(duration))
                        if not v.ok:
                            show_error_ar(v.message_ar, v.message_en)
                        elif db.count_overlapping_appointments(conn, doc_id, start, int(duration)) > 0:
                            show_error_ar("تعارض في جدول الطبيب.", "overlap")
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
                                st.success("تم حفظ الموعد.")
                                st.rerun()
                            except Exception as exc:  # noqa: BLE001
                                show_error_ar("فشل الحفظ.", str(exc))
                    else:
                        time_changed = old_start is not None and start != old_start
                        if time_changed:
                            v = validate_new_appointment(start, int(duration))
                            if not v.ok:
                                show_error_ar(v.message_ar, v.message_en)
                            else:
                                vr = validate_reschedule_or_cancel(old_start)
                                if not vr.ok:
                                    show_error_ar(vr.message_ar, vr.message_en)
                                elif (
                                    db.count_overlapping_appointments(
                                        conn, doc_id, start, int(duration), exclude_id=appt_id
                                    )
                                    > 0
                                ):
                                    show_error_ar("تعارض في جدول الطبيب.", "overlap")
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
                                        st.success("تم حفظ الموعد.")
                                        st.rerun()
                                    except Exception as exc:  # noqa: BLE001
                                        show_error_ar("فشل الحفظ.", str(exc))
                        else:
                            if (
                                db.count_overlapping_appointments(
                                    conn, doc_id, start, int(duration), exclude_id=appt_id
                                )
                                > 0
                            ):
                                show_error_ar("تعارض في جدول الطبيب.", "overlap")
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
                                    st.success("تم حفظ الموعد.")
                                    st.rerun()
                                except Exception as exc:  # noqa: BLE001
                                    show_error_ar("فشل الحفظ.", str(exc))

                if mode == "تعديل" and appt_id:
                    if st.button("حذف الموعد نهائياً"):
                        try:
                            db.delete_appointment(conn, appt_id)
                            conn.commit()
                            st.success("تم الحذف.")
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error_ar("فشل الحذف.", str(exc))

    with t3:
        st.markdown("#### أوقات عمل الأطباء (أسبوعي)")
        docs = db.list_doctors(conn)
        doc_pick = st.selectbox("طبيب", docs, format_func=lambda r: r["name"])
        if doc_pick:
            st.write("الأيام: 0=الاثنين … 6=الأحد (وفق تقويم بايثون)")
            rows = db.list_doctor_availability(conn, int(doc_pick["id"]))
            st.dataframe(rows_to_df(rows), use_container_width=True, hide_index=True)
            wd = st.number_input("يوم الأسبوع (0-6)", min_value=0, max_value=6, value=6)
            st1, st2 = st.columns(2)
            with st1:
                s_t = st.text_input("من (HH:MM)", value="08:00")
            with st2:
                e_t = st.text_input("إلى (HH:MM)", value="17:00")
            if st.button("إضافة فترة"):
                try:
                    db.insert_availability(conn, int(doc_pick["id"]), int(wd), s_t, e_t)
                    conn.commit()
                    st.success("تمت الإضافة.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error_ar("تعذرت الإضافة.", str(exc))
            del_id = st.number_input("معرف فترة للحذف (اختياري)", min_value=0, value=0)
            if del_id > 0 and st.button("حذف الفترة"):
                try:
                    db.delete_availability(conn, int(del_id))
                    conn.commit()
                    st.success("تم الحذف.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error_ar("تعذر الحذف.", str(exc))

    with t4:
        st.markdown("#### تقارير")
        df = appointments_dataframe(conn)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("مواعيد اليوم", report_daily_counts(df, today))
        with c2:
            st.metric("معدل الإلغاء", f"{report_cancellation_rate(df)*100:.1f}%")
        with c3:
            wk = 0
            if not df.empty:
                wk = int(
                    ((df["start_at"].dt.date >= week_start) & (df["start_at"].dt.date <= week_end)).sum()
                )
            st.metric("مواعيد الأسبوع (اثنين–أحد)", wk)
        st.markdown("##### أكثر الأطباء ازدحاماً")
        st.dataframe(report_busiest_doctors(df), use_container_width=True, hide_index=True)
        st.markdown("##### أكثر الأسباب شيوعاً")
        st.dataframe(report_top_reasons(df), use_container_width=True, hide_index=True)
        st.markdown("##### تنبيهات خلال 24 ساعة")
        up = report_upcoming_within_hours(df, 24)
        st.dataframe(up, use_container_width=True, hide_index=True)

        st.markdown("#### تصدير CSV")
        table = st.selectbox("جدول", ["appointments", "patients", "doctors", "clinics"])
        try:
            data = conn.execute(f"SELECT * FROM {table}").fetchall()
            pdf = pd.DataFrame([dict(r) for r in data])
            csv_bytes = pdf.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "تنزيل ملف CSV",
                data=csv_bytes,
                file_name=f"{table}.csv",
                mime="text/csv",
                key=f"csv_dl_{table}",
            )
        except Exception as exc:  # noqa: BLE001
            show_error_ar("تعذر تجهيز التصدير.", str(exc))


def doctor_dashboard(conn, user: dict) -> None:
    did = user.get("doctor_id")
    if did is None:
        show_error_ar("هذا الحساب غير مرتبط بطبيب.")
        return
    st.subheader("لوحة الطبيب — مواعيد اليوم")
    day = st.date_input("اليوم", value=date.today())
    rows = db.fetch_doctor_appointments_for_date(conn, did, datetime.combine(day, time.min))
    df = rows_to_df(rows)
    if df.empty:
        st.info("لا توجد مواعيد في هذا اليوم.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        appt_id = st.selectbox("اختر موعداً لتحديث الحالة", df["id"].tolist())
        new_status = st.selectbox(
            "الحالة",
            [
                AppointmentStatus.COMPLETED.value,
                AppointmentStatus.LATE.value,
                AppointmentStatus.CANCELLED.value,
                AppointmentStatus.CONFIRMED.value,
                AppointmentStatus.PENDING.value,
            ],
        )
        if st.button("تحديث الحالة"):
            try:
                db.update_appointment_status(conn, int(appt_id), new_status)
                conn.commit()
                st.success("تم التحديث.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                show_error_ar("فشل التحديث.", str(exc))


def main() -> None:
    st.set_page_config(page_title="جدولة المواعيد", page_icon="🏥", layout="wide")
    inject_rtl_theme()
    conn = get_connection()
    auth.init_session_state()
    user = auth.current_user()

    with st.sidebar:
        st.markdown("### 🏥 نظام المواعيد")
        if user:
            st.write(f"**{user['username']}**")
            st.caption(str(user["role"].value))
            if st.button("تسجيل الخروج"):
                auth.logout_user()
                st.rerun()
        with st.expander("Demo logins (English)"):
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
        st.warning("دور غير معروف.")


if __name__ == "__main__":
    main()
