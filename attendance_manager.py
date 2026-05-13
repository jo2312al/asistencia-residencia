from datetime import datetime, date
import mysql.connector
import pytz

mexico_tz = pytz.timezone('America/Mexico_City')

class AttendanceManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def register_attendance(self, token, project_id, event_type='entrada'):
        try:
            conn = mysql.connector.connect(**self.db_manager.db_config)
            cursor = conn.cursor(dictionary=True)

            cursor.execute("SELECT * FROM participants WHERE token = %s AND project_id = %s", (token, project_id))
            participant = cursor.fetchone()
            if not participant:
                conn.close()
                return "Participante no encontrado o token inválido para este proyecto."

            participant_id = participant['id']
            now_mx = datetime.now(mexico_tz)
            today_mx = now_mx.date()

            cursor.execute("""
                SELECT id FROM attendance_events
                WHERE participant_id = %s AND project_id = %s AND event_type = %s
                AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s
            """, (participant_id, project_id, event_type, today_mx))

            if cursor.fetchone():
                conn.close()
                return f"El participante ya registró su {event_type} hoy."

            cursor.execute("""
                INSERT INTO attendance_events (participant_id, project_id, event_type, timestamp)
                VALUES (%s, %s, %s, %s)
            """, (participant_id, project_id, event_type, now_mx))
            conn.commit()
            conn.close()
            return "Asistencia registrada exitosamente"
        except Exception as e:
            if 'conn' in locals() and conn.is_connected():
                conn.close()
            return f"Error al registrar asistencia: {str(e)}"

    def get_attendance_report(self, start_date=None, end_date=None, project_id=None):
        conn = mysql.connector.connect(**self.db_manager.db_config)
        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT p.full_name, p.email, p.phone, pr.name as project_name, a.event_type,
                   CONVERT_TZ(a.timestamp, '+00:00', '-06:00') AS local_timestamp
            FROM attendance_events a
            JOIN participants p ON a.participant_id = p.id
            JOIN projects pr ON a.project_id = pr.id
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
            query += " AND a.project_id = %s"
            params.append(project_id)

        query += " ORDER BY a.timestamp DESC"
        cursor.execute(query, params)
        report = cursor.fetchall()
        conn.close()
        return report
