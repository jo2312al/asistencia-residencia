from datetime import datetime, date
import mysql.connector
import pytz

# Define zona horaria de Ciudad de México
mexico_tz = pytz.timezone('America/Mexico_City')

class AttendanceManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def register_attendance(self, student_id):
        student = self.db_manager.get_student_by_matricula(student_id)
        if not student:
            raise ValueError("Estudiante no encontrado")

        student_id = student[0]

        conn = mysql.connector.connect(**self.db_manager.db_config)
        cursor = conn.cursor()

        # Obtener la hora actual en zona horaria de México
        now_mx = datetime.now(mexico_tz)
        today_mx = now_mx.date()

        # Verifica si ya registró asistencia hoy
        cursor.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id = %s AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s",
            (student_id, today_mx)
        )
        if cursor.fetchone()[0] > 0:
            conn.close()
            raise ValueError("El estudiante ya registró asistencia hoy")

        # Registrar la asistencia
        cursor.execute(
            "INSERT INTO attendance (student_id, timestamp) VALUES (%s, %s)",
            (student_id, now_mx)
        )
        conn.commit()
        conn.close()
        return "Asistencia registrada exitosamente"

    def register_attendance_by_matricula(self, matricula):
        try:
            conn = mysql.connector.connect(**self.db_manager.db_config)
            cursor = conn.cursor(dictionary=True)

            cursor.execute("SELECT id FROM students WHERE matricula = %s", (matricula,))
            student = cursor.fetchone()
            if not student:
                conn.close()
                return "Estudiante no encontrado"

            student_id = student['id']

            # Hora actual en zona horaria de México
            now_mx = datetime.now(mexico_tz)
            today_mx = now_mx.date()

            # Verifica si ya registró asistencia hoy
            cursor.execute("""
                SELECT id FROM attendance 
                WHERE student_id = %s AND DATE(CONVERT_TZ(timestamp, '+00:00', '-06:00')) = %s
            """, (student_id, today_mx))
            if cursor.fetchone():
                conn.close()
                return "Este alumno ya fue tomado asistencia"

            # Insertar registro de asistencia
            cursor.execute("""
                INSERT INTO attendance (student_id, timestamp)
                VALUES (%s, %s)
            """, (student_id, now_mx))
            conn.commit()
            conn.close()
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
