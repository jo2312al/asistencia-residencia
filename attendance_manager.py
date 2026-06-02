from datetime import datetime

import mysql.connector
import pytz


mexico_tz = pytz.timezone("America/Mexico_City")
SUCCESS_MESSAGE = "Asistencia registrada exitosamente"
DUPLICATE_MESSAGE = "Este alumno ya fue tomado asistencia"


class AttendanceManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _connect(self):
        return mysql.connector.connect(**self.db_manager.db_config)

    def _now(self):
        now_mx = datetime.now(mexico_tz)
        return now_mx, now_mx.date()

    def _register_legacy_attendance(self, cursor, student_id, now_mx, today_mx):
        if self._has_attendance_today(cursor, student_id, today_mx):
            return None
        return self._insert_attendance(cursor, student_id, now_mx)

    def _has_attendance_today(self, cursor, student_id, today_mx):
        cursor.execute(
            """SELECT id FROM attendance
               WHERE student_id = %s AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s""",
            (student_id, today_mx),
        )
        return cursor.fetchone() is not None

    def _insert_attendance(self, cursor, student_id, now_mx):
        cursor.execute(
            "INSERT INTO attendance (student_id, timestamp) VALUES (%s, %s)",
            (student_id, now_mx),
        )
        return cursor.lastrowid

    def register_attendance(self, student_id):
        student = self.db_manager.get_student_by_matricula(student_id)
        if not student:
            raise ValueError("Estudiante no encontrado")
        return self._register_required_student(student[0])

    def _register_required_student(self, student_id):
        result = self._register_student_id(student_id)
        if not result["attendance_id"]:
            raise ValueError("El estudiante ya registro asistencia hoy")
        return SUCCESS_MESSAGE

    def _register_student_id(self, student_id):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            now_mx, today_mx = self._now()
            attendance_id = self._register_legacy_attendance(cursor, student_id, now_mx, today_mx)
            if attendance_id:
                conn.commit()
            return {"attendance_id": attendance_id, "now_mx": now_mx}
        finally:
            conn.close()

    def register_attendance_by_qr_data(self, qr_data, event_id=None, event_type="entrada"):
        credential = self._get_credential(qr_data)
        if not credential:
            return self.register_attendance_by_matricula(qr_data, event_id, event_type)
        return self._register_credential_attendance(credential, event_id, event_type)

    def _get_credential(self, qr_data):
        try:
            return self.db_manager.get_credential_by_token(qr_data)
        except Exception:
            return None

    def _register_credential_attendance(self, credential, event_id, event_type):
        invalid = self._credential_error(credential)
        if invalid:
            return invalid
        return self._record_credential_attendance(credential, event_id, event_type)

    def _credential_error(self, credential):
        if credential["credential_status"] != "active" or credential["participant_status"] != "active":
            return "Credencial inactiva"
        if not credential["legacy_student_id"]:
            return "Credencial sin alumno vinculado"
        return None

    def _record_credential_attendance(self, credential, event_id, event_type):
        try:
            result = self._register_student_id(credential["legacy_student_id"])
            if not result["attendance_id"]:
                return DUPLICATE_MESSAGE
            self._record_attendance_event(credential, result, event_id, event_type)
            return SUCCESS_MESSAGE
        except Exception as error:
            return f"Error al registrar asistencia: {error}"

    def _record_attendance_event(self, credential, result, event_id, event_type):
        self.db_manager.record_attendance_event(
            credential["participant_id"],
            credential["credential_id"],
            result["attendance_id"],
            event_type or "entrada",
            result["now_mx"],
            event_id,
        )

    def register_attendance_by_matricula(self, matricula, event_id=None, event_type="entrada"):
        try:
            student = self._get_student_for_attendance(matricula)
            if not student:
                return "Estudiante no encontrado"
            return self._register_student_attendance(student, event_id, event_type)
        except Exception as error:
            return f"Error al registrar asistencia: {error}"

    def _get_student_for_attendance(self, matricula):
        conn = self._connect()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(self._student_lookup_query(), (matricula,))
            return cursor.fetchone()
        finally:
            conn.close()

    def _student_lookup_query(self):
        return """SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id
                  FROM students WHERE matricula = %s"""

    def _register_student_attendance(self, student, event_id, event_type):
        result = self._register_student_id(student["id"])
        if not result["attendance_id"]:
            return DUPLICATE_MESSAGE
        self._record_student_event_if_needed(student, result, event_id, event_type)
        return SUCCESS_MESSAGE

    def _record_student_event_if_needed(self, student, result, event_id, event_type):
        if not event_id:
            return
        try:
            credential = self._ensure_student_credential(student)
            self._record_student_event(credential, result, event_id, event_type)
        except Exception:
            pass

    def _ensure_student_credential(self, student):
        credential = self.db_manager.ensure_student_participant_credential(self._student_tuple(student))
        return self.db_manager.get_credential_by_token(credential["token"])

    def _student_tuple(self, student):
        return (
            student["id"],
            student["first_name"],
            student["last_name_p"],
            student["last_name_m"],
            student["matricula"],
            student["carrera"],
            student["project_id"],
        )

    def _record_student_event(self, credential, result, event_id, event_type):
        if not credential:
            return
        self.db_manager.record_attendance_event(
            credential["participant_id"],
            credential["credential_id"],
            result["attendance_id"],
            event_type or "entrada",
            result["now_mx"],
            event_id,
        )

    def get_attendance_report(self, start_date=None, end_date=None, project_id=None):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            query, params = self._attendance_report_query(start_date, end_date, project_id)
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    def _attendance_report_query(self, start_date, end_date, project_id):
        query = [self._attendance_report_base_query()]
        params = []
        self._append_report_filters(query, params, start_date, end_date, project_id)
        query.append("ORDER BY a.timestamp DESC")
        return " ".join(query), params

    def _attendance_report_base_query(self):
        return """SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name,
                         CONVERT_TZ(a.timestamp, '+00:00', '-06:00') AS local_timestamp
                  FROM attendance a
                  JOIN students s ON a.student_id = s.id
                  LEFT JOIN projects p ON s.project_id = p.id
                  WHERE 1=1"""

    def _append_report_filters(self, query, params, start_date, end_date, project_id):
        if start_date:
            query.append("AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) >= %s")
            params.append(start_date)
        if end_date:
            query.append("AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) <= %s")
            params.append(end_date)
        if project_id:
            query.append("AND s.project_id = %s")
            params.append(project_id)
