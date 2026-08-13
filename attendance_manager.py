from datetime import datetime

import pytz


mexico_tz = pytz.timezone("America/Mexico_City")
SUCCESS_MESSAGE = "Asistencia registrada exitosamente"
DUPLICATE_MESSAGE = "Este alumno ya fue tomado asistencia"


class GestorAsistencia:
    def __init__(self, db_manager):
        """Realiza internamente la operación init."""
        self.db_manager = db_manager

    def _conectar(self):
        """Realiza internamente la operación conectar."""
        return self.db_manager.conectar()

    def _ahora(self):
        """Realiza internamente la operación now."""
        now_mx = datetime.now(mexico_tz)
        return now_mx, now_mx.date()

    def _registrar_heredado_asistencia(self, cursor, student_id, now_mx, today_mx):
        """Realiza internamente la operación registrar heredado asistencia."""
        if self._tiene_asistencia_today(cursor, student_id, today_mx):
            return None
        return self._insertar_asistencia(cursor, student_id, now_mx)

    def _tiene_asistencia_today(self, cursor, student_id, today_mx):
        """Realiza internamente la operación has attendance today."""
        cursor.execute(
            """SELECT id FROM attendance
               WHERE student_id = %s AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s""",
            (student_id, today_mx),
        )
        return cursor.fetchone() is not None

    def _insertar_asistencia(self, cursor, student_id, now_mx):
        """Realiza internamente la operación insert attendance."""
        cursor.execute(
            "INSERT INTO attendance (student_id, timestamp) VALUES (%s, %s)",
            (student_id, now_mx),
        )
        return cursor.lastrowid

    def registrar_asistencia(self, student_id):
        """Ejecuta la operación registrar asistencia y devuelve el resultado correspondiente."""
        student = self.db_manager.obtener_participante_por_matricula(student_id)
        if not student:
            raise ValueError("Estudiante no encontrado")
        return self._registrar_requeridos_participante(student[0])

    def _registrar_requeridos_participante(self, student_id):
        """Realiza internamente la operación registrar requeridos participante."""
        result = self._registrar_participante_id(student_id)
        if not result["attendance_id"]:
            raise ValueError("El estudiante ya registro asistencia hoy")
        return SUCCESS_MESSAGE

    def _registrar_participante_id(self, student_id):
        """Realiza internamente la operación registrar participante id."""
        conn = self._conectar()
        try:
            cursor = conn.cursor()
            now_mx, today_mx = self._ahora()
            attendance_id = self._registrar_heredado_asistencia(cursor, student_id, now_mx, today_mx)
            if attendance_id:
                conn.commit()
            return {"attendance_id": attendance_id, "now_mx": now_mx}
        finally:
            conn.close()

    def registrar_por_datos_qr(self, qr_data, event_id=None, event_type="entrada"):
        """Ejecuta la operación registrar por datos qr y devuelve el resultado correspondiente."""
        credential = self._obtener_credencial(qr_data)
        if not credential:
            return self.registrar_por_matricula(qr_data, event_id, event_type)
        return self._registrar_credencial_asistencia_operacion(credential, event_id, event_type)

    def _obtener_credencial(self, qr_data):
        """Realiza internamente la operación obtener credencial."""
        try:
            return self.db_manager.obtener_credencial_por_token(qr_data)
        except Exception:
            return None

    def _registrar_credencial_asistencia_operacion(self, credential, event_id, event_type):
        """Realiza internamente la operación registrar credencial asistencia operacion."""
        invalid = self._credencial_error(credential)
        if invalid:
            return invalid
        return self._registrar_credencial_asistencia(credential, event_id, event_type)

    def _credencial_error(self, credential):
        """Realiza internamente la operación credential error."""
        if credential["credential_status"] != "active" or credential["participant_status"] != "active":
            return "Credencial inactiva"
        if not credential["legacy_student_id"]:
            return "Credencial sin alumno vinculado"
        return None

    def _registrar_credencial_asistencia(self, credential, event_id, event_type):
        """Realiza internamente la operación registrar credencial asistencia."""
        try:
            result = self._registrar_participante_id(credential["legacy_student_id"])
            if not result["attendance_id"]:
                return self._duplicado_message(credential, event_id, event_type)
            self._registrar_asistencia_evento(credential, result, event_id, event_type)
            return SUCCESS_MESSAGE
        except Exception as error:
            return f"Error al registrar asistencia: {error}"

    def _duplicado_message(self, credential, event_id, event_type):
        """Realiza internamente la operación duplicate message."""
        ultimo = self.db_manager.ultima_asistencia_participante(credential["participant_id"], event_id, event_type)
        if not ultimo or not ultimo.get("timestamp"):
            return DUPLICATE_MESSAGE
        hora = ultimo["timestamp"].strftime("%H:%M")
        tipo = ultimo.get("event_type") or event_type or "entrada"
        return f"Asistencia duplicada: {tipo} ya registrada a las {hora}"

    def _registrar_asistencia_evento(self, credential, result, event_id, event_type):
        """Realiza internamente la operación registrar asistencia evento."""
        self.db_manager.registrar_asistencia_evento(
            credential["participant_id"],
            credential["credential_id"],
            result["attendance_id"],
            event_type or "entrada",
            result["now_mx"],
            event_id,
        )

    def registrar_por_matricula(self, matricula, event_id=None, event_type="entrada"):
        """Ejecuta la operación registrar por matricula y devuelve el resultado correspondiente."""
        try:
            student = self._obtener_participante_para_asistencia(matricula)
            if not student:
                return "Estudiante no encontrado"
            return self._registrar_participante_asistencia(student, event_id, event_type)
        except Exception as error:
            return f"Error al registrar asistencia: {error}"

    def _obtener_participante_para_asistencia(self, matricula):
        """Realiza internamente la operación obtener participante para asistencia."""
        conn = self._conectar()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(self._participante_lookup_query(), (matricula,))
            return cursor.fetchone()
        finally:
            conn.close()

    def _participante_lookup_query(self):
        """Realiza internamente la operación student lookup query."""
        return """SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id
                  FROM students WHERE matricula = %s"""

    def _registrar_participante_asistencia(self, student, event_id, event_type):
        """Realiza internamente la operación registrar participante asistencia."""
        result = self._registrar_participante_id(student["id"])
        if not result["attendance_id"]:
            return DUPLICATE_MESSAGE
        self._registrar_participante_evento_si_necesario(student, result, event_id, event_type)
        return SUCCESS_MESSAGE

    def _registrar_participante_evento_si_necesario(self, student, result, event_id, event_type):
        """Realiza internamente la operación registrar participante evento si necesario."""
        if not event_id:
            return
        try:
            credential = self._asegurar_participante_credencial(student)
            self._registrar_participante_evento(credential, result, event_id, event_type)
        except Exception:
            pass

    def _asegurar_participante_credencial(self, student):
        """Realiza internamente la operación asegurar participante credencial."""
        credential = self.db_manager.asegurar_participante_participante_credencial(self._participante_tuple(student))
        return self.db_manager.obtener_credencial_por_token(credential["token"])

    def _participante_tuple(self, student):
        """Realiza internamente la operación student tuple."""
        return (
            student["id"],
            student["first_name"],
            student["last_name_p"],
            student["last_name_m"],
            student["matricula"],
            student["carrera"],
            student["project_id"],
        )

    def _registrar_participante_evento(self, credential, result, event_id, event_type):
        """Realiza internamente la operación registrar participante evento."""
        if not credential:
            return
        self.db_manager.registrar_asistencia_evento(
            credential["participant_id"],
            credential["credential_id"],
            result["attendance_id"],
            event_type or "entrada",
            result["now_mx"],
            event_id,
        )

    def obtener_reporte(self, start_date=None, end_date=None, project_id=None):
        """Ejecuta la operación obtener reporte y devuelve el resultado correspondiente."""
        conn = self._conectar()
        try:
            cursor = conn.cursor()
            query, params = self._asistencia_reporte_query(start_date, end_date, project_id)
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    def _asistencia_reporte_query(self, start_date, end_date, project_id):
        """Realiza internamente la operación attendance report query."""
        query = [self._asistencia_reporte_base_query()]
        params = []
        self._agregar_reporte_filtros(query, params, start_date, end_date, project_id)
        query.append("ORDER BY a.timestamp DESC")
        return " ".join(query), params

    def _asistencia_reporte_base_query(self):
        """Realiza internamente la operación attendance report base query."""
        return """SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name,
                         CONVERT_TZ(a.timestamp, '+00:00', '-06:00') AS local_timestamp
                  FROM attendance a
                  JOIN students s ON a.student_id = s.id
                  LEFT JOIN projects p ON s.project_id = p.id
                  WHERE 1=1"""

    def _agregar_reporte_filtros(self, query, params, start_date, end_date, project_id):
        """Realiza internamente la operación append report filters."""
        if start_date:
            query.append("AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) >= %s")
            params.append(start_date)
        if end_date:
            query.append("AND DATE(CONVERT_TZ(a.timestamp, '+00:00', '-06:00')) <= %s")
            params.append(end_date)
        if project_id:
            query.append("AND s.project_id = %s")
            params.append(project_id)


# Compatibilidad con la API y el nombre históricos.
GestorAsistencia.registrar_asistencia = GestorAsistencia.registrar_asistencia
GestorAsistencia.register_attendance_by_qr_data = GestorAsistencia.registrar_por_datos_qr
GestorAsistencia.register_attendance_by_matricula = GestorAsistencia.registrar_por_matricula
GestorAsistencia.get_attendance_report = GestorAsistencia.obtener_reporte
AttendanceManager = GestorAsistencia

# Alias temporales para compatibilidad con la API anterior.
GestorAsistencia._connect = GestorAsistencia._conectar
GestorAsistencia._ensure_student_credential = GestorAsistencia._asegurar_participante_credencial
GestorAsistencia._get_credential = GestorAsistencia._obtener_credencial
GestorAsistencia._get_student_for_attendance = GestorAsistencia._obtener_participante_para_asistencia
GestorAsistencia._record_attendance_event = GestorAsistencia._registrar_asistencia_evento
GestorAsistencia._record_credential_attendance = GestorAsistencia._registrar_credencial_asistencia
GestorAsistencia._record_student_event = GestorAsistencia._registrar_participante_evento
GestorAsistencia._record_student_event_if_needed = GestorAsistencia._registrar_participante_evento_si_necesario
GestorAsistencia._register_credential_attendance = GestorAsistencia._registrar_credencial_asistencia_operacion
GestorAsistencia._register_legacy_attendance = GestorAsistencia._registrar_heredado_asistencia
GestorAsistencia._register_required_student = GestorAsistencia._registrar_requeridos_participante
GestorAsistencia._register_student_attendance = GestorAsistencia._registrar_participante_asistencia
GestorAsistencia._register_student_id = GestorAsistencia._registrar_participante_id
GestorAsistencia.register_attendance = GestorAsistencia.registrar_asistencia

# Alias temporales para compatibilidad con la API anterior.
GestorAsistencia._append_report_filters = GestorAsistencia._agregar_reporte_filtros
GestorAsistencia._attendance_report_base_query = GestorAsistencia._asistencia_reporte_base_query
GestorAsistencia._attendance_report_query = GestorAsistencia._asistencia_reporte_query
GestorAsistencia._credential_error = GestorAsistencia._credencial_error
GestorAsistencia._duplicate_message = GestorAsistencia._duplicado_message
GestorAsistencia._has_attendance_today = GestorAsistencia._tiene_asistencia_today
GestorAsistencia._insert_attendance = GestorAsistencia._insertar_asistencia
GestorAsistencia._now = GestorAsistencia._ahora
GestorAsistencia._student_lookup_query = GestorAsistencia._participante_lookup_query
GestorAsistencia._student_tuple = GestorAsistencia._participante_tuple
