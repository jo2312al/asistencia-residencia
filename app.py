import pandas as pd
from werkzeug.utils import secure_filename
import uuid
import os
from flask import Flask, request, jsonify, render_template, redirect, send_file, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from database import DatabaseManager
from qr_manager import QRManager
from attendance_manager import AttendanceManager
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import xlsxwriter
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key'  # Cambia por una clave segura
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2312',  # Actualizada según tu código
    'database': 'innovatec'
}
db_manager = DatabaseManager(db_config)
qr_manager = QRManager()
attendance_manager = AttendanceManager(db_manager)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user(username):
    user_data = db_manager.get_user(username)
    if user_data:
        return User(username)
    return None

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_data = db_manager.get_user(username)
        if user_data and check_password_hash(user_data[1], password):
            login_user(User(username))
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            if not username or not password:
                return jsonify({'success': False, 'error': 'Usuario y contraseña son requeridos'}), 400
            
            if db_manager.get_user(username):
                return jsonify({'success': False, 'error': 'El usuario ya existe'}), 400
            
            db_manager.add_user(username, password)  # La contraseña se hashea en database.py
            return jsonify({'success': True, 'message': 'Usuario creado exitosamente'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    return render_template('create_user.html')

@app.route('/register')
@login_required
def register():
    projects = db_manager.get_all_projects()
    return render_template('register.html', projects=projects)

@app.route('/scan')
@login_required
def scan():
    return render_template('scan.html')

@app.route('/dashboard')
@login_required
def dashboard():
    projects = db_manager.get_all_projects()
    return render_template('dashboard.html', projects=projects)

@app.route('/reports')
@login_required
def reports():
    projects = db_manager.get_all_projects()
    return render_template('reports.html', projects=projects)

@app.route('/data')
@login_required
def data():
    students = db_manager.get_all_students()
    return render_template('data.html', students=students)

@app.route('/generate_qr', methods=['POST'])
@login_required
def generate_qr():
    data = request.form
    student_id = str(uuid.uuid4())
    try:
        first_name = data['first_name']
        last_name_p = data['last_name_p']
        last_name_m = data['last_name_m']
        matricula = data['matricula']
        carrera = data['carrera']
        project_id = data.get('project_id') or None

        if project_id:
            conn = mysql.connector.connect(**db_config)
            c = conn.cursor()
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                return jsonify({'success': False, 'error': f'Proyecto con ID {project_id} no existe'}), 400
            conn.close()

        db_manager.add_student(student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id)
        qr_path = qr_manager.generate_qr(matricula)
        return jsonify({'success': True, 'qr_path': qr_path})
    except KeyError as e:
        return jsonify({'success': False, 'error': f'Falta el campo {str(e)}'}), 400
    except mysql.connector.Error as e:
        if e.errno == 1062:
            return jsonify({'success': False, 'error': f'La matrícula {matricula} ya está registrada'}), 400
        return jsonify({'success': False, 'error': 'Error en la base de datos'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/upload_excel', methods=['POST'])
@login_required
def upload_excel():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No se proporcionó un archivo Excel'}), 400
        file = request.files['file']
        project_id = request.form.get('project_id') or None
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No se seleccionó un archivo'}), 400
        if project_id:
            conn = mysql.connector.connect(**db_config)
            c = conn.cursor()
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                return jsonify({'success': False, 'error': 'Proyecto no encontrado'}), 400
            conn.close()

        result = db_manager.upload_students_from_excel(file, project_id)
        return jsonify({'success': True, 'message': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/register_attendance', methods=['POST'])
@login_required
def register_attendance():
    try:
        data = request.get_json()
        if not data or 'qr_data' not in data:
            return jsonify({'success': False, 'error': 'Datos de QR no proporcionados'}), 400
        
        qr_data = data['qr_data']
        result = attendance_manager.register_attendance_by_matricula(qr_data)
        if result == "Asistencia registrada exitosamente":
            return jsonify({'success': True, 'message': result})
        elif result == "Este alumno ya fue tomado asistencia":
            return jsonify({'success': False, 'error': result})
        else:
            return jsonify({'success': False, 'error': result}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/get_projects')
@login_required
def get_projects():
    projects = db_manager.get_all_projects()
    return jsonify([{'id': p.id, 'name': p.name} for p in projects])

@app.route('/export_excel')
@login_required
def export_excel():
    project_id = request.args.get('project_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    try:
        file_path = db_manager.export_attendance_to_excel(project_id, start_date, end_date)
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/export_pdf')
@login_required
def export_pdf():
    project_id = request.args.get('project_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    try:
        file_path = db_manager.export_attendance_to_pdf(project_id, start_date, end_date)
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/generate_report', methods=['POST'])
@login_required
def generate_report():
    try:
        data = request.form
        start_date = data.get('start_date') or None
        end_date = data.get('end_date') or None
        project_id = data.get('project_id') or None
        format_type = data.get('format')

        report_data = attendance_manager.get_attendance_report(start_date, end_date, project_id)
        if not report_data:
            return jsonify({'success': False, 'error': 'No hay datos para el reporte'}), 400

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

        if format_type == 'pdf':
            report_path = os.path.join(report_dir, f'report_{timestamp}.pdf').replace('\\', '/')
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

        elif format_type == 'excel':
            report_path = os.path.join(report_dir, f'report_{timestamp}.xlsx').replace('\\', '/')
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

        else:
            return jsonify({'success': False, 'error': 'Formato no soportado'}), 400

        return jsonify({'success': True, 'report_path': report_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, port=5173)