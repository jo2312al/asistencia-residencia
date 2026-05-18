from datetime import datetime

import mysql.connector
import pytz


mexico_tz = pytz.timezone('America/Mexico_City')


class AttendanceManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def _register_legacy_attendance(self, cursor, student_id, now_mx, today_mx):
        cursor.execute("""
            SELECT id FROM attendance
            WHERE student_id = %s AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s
        """, (student_id, today_mx))
        if cursor.fetchone():
            return None

        cursor.execute("""
            INSERT INTO attendance (student_id, timestamp)
            VALUES (%s, %s)
        """, (student_id, now_mx))
        return cursor.lastrowid

    def register_attendance(self, student_id):
        student = self.db_manager.get_student_by_matricula(student_id)
        if not student:
            raise ValueError("Estudiante no encontrado")

        conn = mysql.connector.connect(**self.db_manager.db_config)
        cursor = conn.cursor()
        now_mx = datetime.now(mexico_tz)
        today_mx = now_mx.date()
        attendance_id = self._register_legacy_attendance(cursor, student[0], now_mx, today_mx)
        if not attendance_id:
            conn.close()
            raise ValueError("El estudiante ya registro asistencia hoy")

        conn.commit()
        conn.close()
        return "Asistencia registrada exitosamente"

    def register_attendance_by_qr_data(self, qr_data, event_id=None):
        try:
            credential = self.db_manager.get_credential_by_token(qr_data)
        except Exception:
            credential = None
        if not credential:
            return self.register_attendance_by_matricula(qr_data, event_id)

        if credential['credential_status'] != 'active' or credential['participant_status'] != 'active':
            return "Credencial inactiva"

        legacy_student_id = credential['legacy_student_id']
        if not legacy_student_id:
            return "Credencial sin alumno vinculado"

        try:
            conn = mysql.connector.connect(**self.db_manager.db_config)
            cursor = conn.cursor()
            now_mx = datetime.now(mexico_tz)
            today_mx = now_mx.date()
            attendance_id = self._register_legacy_attendance(cursor, legacy_student_id, now_mx, today_mx)
            if not attendance_id:
                conn.close()
                return "Este alumno ya fue tomado asistencia"

            conn.commit()
            conn.close()
            self.db_manager.record_attendance_event(
                credential['participant_id'],
                credential['credential_id'],
                attendance_id,
                'entrada',
                now_mx,
                event_id
            )
            return "Asistencia registrada exitosamente"
        except Exception as e:
            if 'conn' in locals() and conn.is_connected():
                conn.close()
            return f"Error al registrar asistencia: {str(e)}"

    def register_attendance_by_matricula(self, matricula, event_id=None):
        try:
            conn = mysql.connector.connect(**self.db_manager.db_config)
            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                "SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id FROM students WHERE matricula = %s",
                (matricula,)
            )
            student = cursor.fetchone()
            if not student:
                conn.close()
                return "Estudiante no encontrado"

            now_mx = datetime.now(mexico_tz)
            today_mx = now_mx.date()
            attendance_id = self._register_legacy_attendance(cursor, student['id'], now_mx, today_mx)
            if not attendance_id:
                conn.close()
                return "Este alumno ya fue tomado asistencia"

            conn.commit()
            conn.close()
            if event_id:
                try:
                    student_tuple = (
                        student['id'],
                        student['first_name'],
                        student['last_name_p'],
                        student['last_name_m'],
                        student['matricula'],
                        student['carrera'],
                        student['project_id'],
                    )
                    credential = self.db_manager.ensure_student_participant_credential(student_tuple)
                    db_credential = self.db_manager.get_credential_by_token(credential['token'])
                    self.db_manager.record_attendance_event(
                        db_credential['participant_id'] if db_credential else None,
                        db_credential['credential_id'] if db_credential else None,
                        attendance_id,
                        'entrada',
                        now_mx,
                        event_id
                    )
                except Exception:
                    pass
            return "Asistencia registrada exitosamente"
        except Exception as e:
            if 'conn' in locals() and conn.is_connected():
                conn.close()
            return f"Error al registrar asistencia: {str(e)}"

    def get_attendance_report(self, start_date=None, end_date=None, project_id=None):
        conn = mysql.connector.connect(**self.db_manager.db_config)
        cursor = conn.cursor()

        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name,
                   CONVERT_TZ(a.timestamp, '+00:00', '-06:00') AS local_timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)

        query += " ORDER BY a.timestamp DESC"
        cursor.execute(query, params)
        report = cursor.fetchall()
        conn.close()
        return report
