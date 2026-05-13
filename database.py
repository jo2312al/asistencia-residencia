import mysql.connector
from mysql.connector import Error
import pandas as pd
import uuid
import os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import xlsxwriter
from werkzeug.security import generate_password_hash

class DatabaseManager:
    VALID_ROLES = {'superadmin', 'admin_proyecto', 'staff', 'consulta', 'participante', 'admin', 'guest'}

    def __init__(self, db_config):
        self.db_config = db_config
        self.init_db()

    def init_db(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(50) PRIMARY KEY,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'guest')''')

        c.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) UNIQUE NOT NULL,
            description TEXT,
            start_date DATE,
            end_date DATE,
            location VARCHAR(255),
            logo_url VARCHAR(255),
            status VARCHAR(20) DEFAULT 'activo',
            sender_email VARCHAR(100),
            email_template TEXT,
            attendance_rules TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS project_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            project_id INT,
            name VARCHAR(100) NOT NULL,
            type VARCHAR(50) NOT NULL,
            is_required BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)''')

        c.execute('''CREATE TABLE IF NOT EXISTS participants (
            id VARCHAR(36) PRIMARY KEY,
            full_name VARCHAR(255) NOT NULL,
            email VARCHAR(100),
            phone VARCHAR(20),
            project_id INT,
            status VARCHAR(50) DEFAULT 'registrado',
            token VARCHAR(100) UNIQUE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)''')

        c.execute('''CREATE TABLE IF NOT EXISTS participant_field_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            field_id INT,
            value TEXT,
            FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
            FOREIGN KEY (field_id) REFERENCES project_fields(id) ON DELETE CASCADE)''')

        c.execute('''CREATE TABLE IF NOT EXISTS credentials (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            token VARCHAR(100) UNIQUE NOT NULL,
            qr_file VARCHAR(255),
            status VARCHAR(50) DEFAULT 'generada',
            FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE)''')

        c.execute('''CREATE TABLE IF NOT EXISTS attendance_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            project_id INT,
            event_type VARCHAR(50) NOT NULL,
            timestamp DATETIME,
            FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)''')

        c.execute('''CREATE TABLE IF NOT EXISTS email_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            status VARCHAR(50),
            timestamp DATETIME,
            FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE)''')

        c.execute('''
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'users'
            AND COLUMN_NAME = 'role'
        ''', (self.db_config['database'],))
        has_role_column = c.fetchone()[0] > 0
        if not has_role_column:
            c.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'guest'")
        conn.commit()
        conn.close()

    def normalize_role(self, role):
        role_value = (role or 'guest').strip().lower()
        if role_value not in self.VALID_ROLES:
            raise ValueError("Rol invalido. Roles permitidos: admin, staff, guest")
        return role_value

    def get_user(self, username):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT username, password_hash, role FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()
        return user

    def add_user(self, username, password_hash, role='guest'):
        role_value = self.normalize_role(role)
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, password_hash, role_value)
        )
        conn.commit()
        conn.close()

    def ensure_user(self, username, password_hash, role='guest'):
        role_value = self.normalize_role(role)
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE username = %s", (username,))
        exists = c.fetchone() is not None
        if not exists:
            c.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (username, password_hash, role_value)
            )
            conn.commit()
        conn.close()
        return not exists

    def add_student(self, student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, project_id)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                  (student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id))
        conn.commit()
        conn.close()

    def get_all_projects(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id, name, description FROM projects")
        projects = c.fetchall()
        conn.close()
        return projects

    def get_total_students(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM students")
        total = c.fetchone()[0]
        conn.close()
        return total

    def get_total_attendance(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM attendance")
        total = c.fetchone()[0]
        conn.close()
        return total

    def get_all_students(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id FROM students")
        students = c.fetchall()
        conn.close()
        return students

    def get_student_by_matricula(self, matricula):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id FROM students WHERE matricula = %s", (matricula,))
        student = c.fetchone()
        conn.close()
        return student

    def add_project(self, name, description=None, start_date=None, end_date=None, location=None, logo_url=None, status='activo', sender_email=None, email_template=None, attendance_rules=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO projects (name, description, start_date, end_date, location, logo_url, status, sender_email, email_template, attendance_rules)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                  (name, description, start_date, end_date, location, logo_url, status, sender_email, email_template, attendance_rules))
        conn.commit()
        project_id = c.lastrowid
        conn.close()
        return project_id

    def get_project(self, project_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor(dictionary=True)
        c.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
        project = c.fetchone()
        conn.close()
        return project

    def add_project_field(self, project_id, name, type, is_required=False):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO project_fields (project_id, name, type, is_required)
                     VALUES (%s, %s, %s, %s)''',
                  (project_id, name, type, is_required))
        conn.commit()
        field_id = c.lastrowid
        conn.close()
        return field_id

    def get_project_fields(self, project_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor(dictionary=True)
        c.execute("SELECT * FROM project_fields WHERE project_id = %s", (project_id,))
        fields = c.fetchall()
        conn.close()
        return fields

    def add_participant(self, participant_id, full_name, email, phone, project_id, token, status='registrado'):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO participants (id, full_name, email, phone, project_id, status, token)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                  (participant_id, full_name, email, phone, project_id, status, token))
        conn.commit()
        conn.close()

    def get_participant_by_token(self, token):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor(dictionary=True)
        c.execute("SELECT * FROM participants WHERE token = %s", (token,))
        participant = c.fetchone()
        conn.close()
        return participant

    def add_participant_field_value(self, participant_id, field_id, value):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO participant_field_values (participant_id, field_id, value)
                     VALUES (%s, %s, %s)''',
                  (participant_id, field_id, value))
        conn.commit()
        conn.close()

    def add_credential(self, participant_id, token, qr_file, status='generada'):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO credentials (participant_id, token, qr_file, status)
                     VALUES (%s, %s, %s, %s)''',
                  (participant_id, token, qr_file, status))
        conn.commit()
        conn.close()

    def log_email(self, participant_id, status):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        from datetime import datetime
        now = datetime.now()
        c.execute('''INSERT INTO email_logs (participant_id, status, timestamp)
                     VALUES (%s, %s, %s)''',
                  (participant_id, status, now))
        conn.commit()
        conn.close()

    def upload_students_from_excel(self, file, project_id):
        df = pd.read_excel(file)
        required_columns = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        if not all(col in df.columns for col in required_columns):
            raise ValueError("El Excel debe contener las columnas: first_name, last_name_p, last_name_m, matricula, carrera")

        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        errors = []
        success_count = 0

        if project_id:
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                raise ValueError("Proyecto no encontrado")

        for index, row in df.iterrows():
            student_id = str(uuid.uuid4())
            matricula = str(row['matricula'])
            try:
                c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, project_id)
                             VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                          (student_id, str(row['first_name']), str(row['last_name_p']), str(row['last_name_m']),
                           matricula, str(row['carrera']), project_id))
                conn.commit()
                success_count += 1
            except mysql.connector.Error as e:
                if e.errno == 1062:
                    errors.append(f"Matrícula {matricula} ya registrada")
                else:
                    errors.append(f"Error en matrícula {matricula}: {str(e)}")
            except Exception as e:
                errors.append(f"Error en matrícula {matricula}: {str(e)}")

        conn.close()
        if errors:
            return f"Procesados {success_count} estudiantes. Errores: {', '.join(errors)}"
        return f"Se procesaron {success_count} estudiantes correctamente"

    def export_attendance_to_excel(self, project_id, start_date, end_date):
        conn = mysql.connector.connect(**self.db_config)
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
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'attendance_report_{timestamp}.xlsx').replace('\\', '/')

        workbook = xlsxwriter.Workbook(report_path)
        worksheet = workbook.add_worksheet()
        worksheet.write('A1', 'Reporte de Asistencias - Innovatec TecNM')
        headers = ['Matrícula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Fecha/Hora']
        for col, header in enumerate(headers):
            worksheet.write(1, col, header)
        for row_idx, row in enumerate(report_data, 2):
            worksheet.write(row_idx, 0, row[0])
            worksheet.write(row_idx, 1, row[1])
            worksheet.write(row_idx, 2, row[2])
            worksheet.write(row_idx, 3, row[3])
            worksheet.write(row_idx, 4, row[4])
            worksheet.write(row_idx, 5, row[5] or 'Sin proyecto')
            worksheet.write(row_idx, 6, row[6].strftime('%Y-%m-%d %H:%M:%S'))
        workbook.close()

        return report_path

    def export_attendance_to_pdf(self, project_id, start_date, end_date):
        conn = mysql.connector.connect(**self.db_config)
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
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'attendance_report_{timestamp}.pdf').replace('\\', '/')

        doc = SimpleDocTemplate(report_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Reporte de Asistencias - Innovatec TecNM", styles['Title']))

        data = [['Matrícula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Fecha/Hora']]
        for row in report_data:
            data.append([
                row[0], row[1], row[2], row[3], row[4], row[5] or 'Sin proyecto', row[6].strftime('%Y-%m-%d %H:%M:%S')
            ])

        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        doc.build(elements)

        return report_path

    def get_all_students_filtered(self, matricula_search='', apellido_p_search='', project_id_filter=''):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        
        query = """
            SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera, s.project_id
            FROM students s
            WHERE 1=1
        """
        params = []
        
        if matricula_search:
            query += " AND s.matricula LIKE %s"
            params.append(f"%{matricula_search}%")
        
        if apellido_p_search:
            query += " AND s.last_name_p LIKE %s"
            params.append(f"%{apellido_p_search}%")
        
        if project_id_filter:
            query += " AND s.project_id = %s"
            params.append(project_id_filter)
        
        c.execute(query, params)
        students = c.fetchall()
        conn.close()
        return students
