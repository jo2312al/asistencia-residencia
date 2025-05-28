from datetime import datetime, date
from datetime import datetime, date
import mysql.connector

class AttendanceManager:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def register_attendance(self, student_id):
        student = self.db_manager.get_student_by_matricula(student_id)
        if not student:
            raise ValueError("Estudiante no encontrado")
        student_id = student[0]
        conn = mysql.connector.connect(**self.db_manager.db_config)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM attendance WHERE student_id = %s AND DATE(timestamp) = %s",
                  (student_id, date.today()))
        if c.fetchone()[0] > 0:
            raise ValueError("El estudiante ya registró asistencia hoy")
        c.execute("INSERT INTO attendance (student_id, timestamp) VALUES (%s, %s)",
                  (student_id, datetime.now()))
        conn.commit()
        conn.close()
        return "Asistencia registrada exitosamente"

    def register_attendance_by_matricula(self, matricula):
        try:
            conn = mysql.connector.connect(**self.db_manager.db_config)
            cursor = conn.cursor(dictionary=True)
            
            # Verificar si el estudiante existe
            cursor.execute("SELECT id FROM students WHERE matricula = %s", (matricula,))
            student = cursor.fetchone()
            if not student:
                conn.close()
                return "Estudiante no encontrado"
            
            student_id = student['id']
            
            # Verificar si ya tiene asistencia hoy
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT id FROM attendance 
                WHERE student_id = %s AND DATE(timestamp) = %s
            """, (student_id, today))
            if cursor.fetchone():
                conn.close()
                return "Este alumno ya fue tomado asistencia"
            
            # Registrar asistencia
            cursor.execute("""
                INSERT INTO attendance (student_id, timestamp)
                VALUES (%s, NOW())
            """, (student_id,))
            conn.commit()
            conn.close()
            return "Asistencia registrada exitosamente"
        except Exception as e:
            if 'conn' in locals() and conn.is_connected():
                conn.close()
            return f"Error al registrar asistencia: {str(e)}"

    def get_attendance_report(self, start_date=None, end_date=None, project_id=None):
        conn = mysql.connector.connect(**self.db_manager.db_config)
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name, a.timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(a.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(a.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        query += " ORDER BY a.timestamp DESC"
        c.execute(query, params)
        report = c.fetchall()
        conn.close()
        return report