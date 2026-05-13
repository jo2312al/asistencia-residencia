from io import BytesIO
import os
import uuid
import zipfile
from datetime import datetime
from functools import wraps

import mysql.connector
import xlsxwriter
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash

from attendance_manager import AttendanceManager
from database import DatabaseManager
from qr_manager import QRManager

VALID_ROLES = ("superadmin", "admin_proyecto", "staff", "consulta", "participante", "admin", "guest")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))


def str_to_bool(value, default=False):
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_db_config():
    ssl_ca_path = os.getenv("DB_SSL_CA", os.path.join(BASE_DIR, "certs", "DigiCertGlobalRootCA.crt.pem"))
    config = {
        "user": os.getenv("DB_USER", "admin2312"),
        "password": os.getenv("DB_PASSWORD", "Josealberto2312"),
        "host": os.getenv("DB_HOST", "innovat.mysql.database.azure.com"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "database": os.getenv("DB_NAME", "innovatec"),
        "ssl_disabled": str_to_bool(os.getenv("DB_SSL_DISABLED"), default=False),
    }
    if ssl_ca_path and os.path.exists(ssl_ca_path):
        config["ssl_ca"] = ssl_ca_path
    return config


def get_role_home_endpoint(role):
    # Fallbacks for legacy users
    if role == 'admin': role = 'superadmin'
    if role == 'guest': role = 'participante'

    role_map = {
        "superadmin": "admin_dashboard",
        "admin_proyecto": "admin_dashboard",
        "staff": "staff_dashboard",
        "consulta": "reports",
        "participante": "guest_dashboard",
    }
    return role_map.get(role, "guest_dashboard")


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

db_config = get_db_config()
db_manager = DatabaseManager(db_config)
qr_manager = QRManager()
attendance_manager = AttendanceManager(db_manager)

default_admin_username = os.getenv("ADMIN_USERNAME")
default_admin_password = os.getenv("ADMIN_PASSWORD")
if default_admin_username and default_admin_password:
    default_hash = generate_password_hash(default_admin_password, method="pbkdf2:sha256")
    db_manager.ensure_user(default_admin_username, default_hash, role="admin")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, username, role="participante"):
        self.id = username
        # Map legacy roles
        if role == 'admin': role = 'superadmin'
        if role == 'guest': role = 'participante'
        self.role = role

    @property
    def is_superadmin(self):
        return self.role == "superadmin"

    @property
    def is_admin_proyecto(self):
        return self.role == "admin_proyecto"

    @property
    def is_staff(self):
        return self.role == "staff"

    @property
    def is_consulta(self):
        return self.role == "consulta"

    @property
    def is_participante(self):
        return self.role == "participante"


def role_required(*roles, api=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                message = "No autorizado para esta accion"
                if api or request.is_json:
                    return jsonify({"success": False, "error": message}), 403
                flash(message, "warning")
                return redirect(url_for("role_home"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


@app.context_processor
def inject_template_helpers():
    return {"VALID_ROLES": VALID_ROLES}


@login_manager.user_loader
def load_user(username):
    user_data = db_manager.get_user(username)
    if user_data:
        return User(user_data[0], user_data[2] or "guest")
    return None


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("role_home"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("role_home"))
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user_data = db_manager.get_user(username)
        if user_data and check_password_hash(user_data[1], password):
            login_user(User(user_data[0], user_data[2] or "guest"))
            return redirect(url_for("role_home"))
        flash("Usuario o contrasena incorrectos", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def role_home():
    return redirect(url_for(get_role_home_endpoint(current_user.role)))


@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for("role_home"))


@app.route("/admin/dashboard")
@login_required
@role_required("superadmin", "admin_proyecto")
def admin_dashboard():
    projects = db_manager.get_all_projects()
    return render_template(
        "admin_dashboard.html",
        projects=projects,
        total_students=db_manager.get_total_students(),
        total_attendance=db_manager.get_total_attendance(),
    )


@app.route("/staff/dashboard")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def staff_dashboard():
    projects = db_manager.get_all_projects()
    return render_template("staff_dashboard.html", projects=projects)


@app.route("/guest/dashboard")
@login_required
@role_required("superadmin", "admin_proyecto", "staff", "consulta", "participante")
def guest_dashboard():
    return render_template("guest_dashboard.html")


@app.route("/create_user", methods=["GET", "POST"])
@login_required
@role_required("superadmin", "admin_proyecto")
def create_user():
    if request.method == "POST":
        try:
            username = request.form.get("username")
            password = request.form.get("password")
            role = request.form.get("role", "guest")
            if not username or not password:
                return jsonify({"success": False, "error": "Usuario y contrasena son requeridos"}), 400

            if db_manager.get_user(username):
                return jsonify({"success": False, "error": "El usuario ya existe"}), 400

            hashed_password = generate_password_hash(password, method="pbkdf2:sha256")
            db_manager.add_user(username, hashed_password, role=role)
            return jsonify({"success": True, "message": "Usuario creado exitosamente"})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return render_template("create_user.html", roles=VALID_ROLES)


@app.route("/register")
@login_required
@role_required("superadmin", "admin_proyecto")
def register():
    projects = db_manager.get_all_projects()
    return render_template("register.html", projects=projects)


@app.route("/scan")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def scan():
    return render_template("scan.html")


@app.route("/reports")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def reports():
    projects = db_manager.get_all_projects()
    return render_template("reports.html", projects=projects)


@app.route("/data")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def data():
    matricula_search = request.args.get("matricula", "").strip()
    apellido_p_search = request.args.get("apellido_p", "").strip()
    project_id_filter = request.args.get("project_id", "")

    page = request.args.get("page", 1, type=int)
    per_page = 10

    projects = db_manager.get_all_projects()
    all_students = db_manager.get_all_students_filtered(matricula_search, apellido_p_search, project_id_filter)

    total_students = len(all_students)
    total_pages = (total_students + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    students_paginated = all_students[start:end]

    students_with_qr = []
    for student in students_paginated:
        matricula = student[4]
        qr_path = os.path.join("static/qr_codes", f"{matricula}.png").replace("\\", "/")

        if not os.path.exists(qr_path):
            qr_manager.generate_qr(matricula)

        project_name = None
        if student[6]:
            for project in projects:
                if project[0] == student[6]:
                    project_name = project[1]
                    break

        student_dict = {
            "id": student[0],
            "first_name": student[1],
            "last_name_p": student[2],
            "last_name_m": student[3],
            "matricula": student[4],
            "carrera": student[5],
            "project_id": student[6],
            "project_name": project_name,
            "qr_path": qr_path if os.path.exists(qr_path) else None,
        }
        students_with_qr.append(student_dict)

    return render_template(
        "data.html",
        students=students_with_qr,
        projects=projects,
        page=page,
        total_pages=total_pages,
        matricula_search=matricula_search,
        apellido_p_search=apellido_p_search,
        project_id_filter=project_id_filter,
    )


@app.route("/download_all_qrs_pdf")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def download_all_qrs_pdf():
    students = db_manager.get_all_students()
    local_qr_manager = QRManager()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = "static/reports"
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    pdf_path = os.path.join(report_dir, f"qr_codes_{timestamp}.pdf").replace("\\", "/")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    projects = {project[0]: project[1] for project in db_manager.get_all_projects()}

    student_data = []
    for student in students:
        matricula = student[4]
        qr_path = os.path.join("static/qr_codes", f"{matricula}.png").replace("\\", "/")

        if not os.path.exists(qr_path):
            local_qr_manager.generate_qr(matricula)

        project_id = student[6]
        project_name = projects.get(project_id, "Sin proyecto") if project_id else "Sin proyecto"
        project_number = (project_id - 1) if project_id else None

        if os.path.exists(qr_path):
            student_data.append(
                {
                    "name": f"{student[1]} {student[2]} {student[3]}",
                    "matricula": matricula,
                    "qr_path": qr_path,
                    "project_id": project_id,
                    "project_name": project_name,
                    "project_number": project_number,
                }
            )

    student_data.sort(key=lambda x: (x["project_id"] is None, x["project_id"] or float("inf")))

    page_width = letter[0]
    page_height = letter[1]
    margin = 0.5 * inch
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin

    cell_width = usable_width / 2
    cell_height = usable_height / 2.5

    for i in range(0, len(student_data), 4):
        students_chunk = student_data[i : i + 4]
        grid_data = [[None, None], [None, None]]

        for j, student in enumerate(students_chunk):
            row = j // 2
            col = j % 2
            cell_elements = []

            name_paragraph = Paragraph(f"Nombre: {student['name']}", styles["Normal"])
            matricula_paragraph = Paragraph(f"Matricula: {student['matricula']}", styles["Normal"])
            cell_elements.append([name_paragraph])
            cell_elements.append([matricula_paragraph])

            project_name_paragraph = Paragraph(f"Proyecto: {student['project_name']}", styles["Normal"])
            number_text = student["project_number"] if student["project_number"] is not None else "N/A"
            project_number_paragraph = Paragraph(f"Numero Proyecto: {number_text}", styles["Normal"])
            cell_elements.append([project_name_paragraph])
            cell_elements.append([project_number_paragraph])
            cell_elements.append([Spacer(1, 0.5 * inch)])

            qr_image = Image(student["qr_path"], width=2 * inch, height=2 * inch)
            qr_image.hAlign = "CENTER"
            cell_elements.append([qr_image])

            sub_table = Table(cell_elements, colWidths=[cell_width])
            sub_table.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            grid_data[row][col] = sub_table

        table = Table(grid_data, colWidths=[cell_width, cell_width], rowHeights=[cell_height, cell_height])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(table)

        if i + 4 < len(student_data):
            elements.append(Spacer(1, 0.5 * inch))

    doc.build(elements)
    return send_file(pdf_path, as_attachment=True, download_name="qr_codes.pdf")


@app.route("/download_all_qrs")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def download_all_qrs():
    students = db_manager.get_all_students()
    local_qr_manager = QRManager()
    memory_file = BytesIO()

    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for student in students:
            matricula = student[4]
            qr_path = os.path.join("static/qr_codes", f"{matricula}.png").replace("\\", "/")

            if not os.path.exists(qr_path):
                local_qr_manager.generate_qr(matricula)

            if os.path.exists(qr_path):
                zf.write(qr_path, arcname=f"qr_codes/{matricula}.png")

    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name="all_qr_codes.zip",
    )


@app.route("/generate_qr", methods=["POST"])
@login_required
@role_required("superadmin", "admin_proyecto", api=True)
def generate_qr():
    data = request.form
    student_id = str(uuid.uuid4())
    try:
        first_name = data["first_name"]
        last_name_p = data["last_name_p"]
        last_name_m = data["last_name_m"]
        matricula = data["matricula"]
        carrera = data["carrera"]
        project_id = data.get("project_id") or None

        if project_id:
            conn = mysql.connector.connect(**db_config)
            c = conn.cursor()
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                return jsonify({"success": False, "error": f"Proyecto con ID {project_id} no existe"}), 400
            conn.close()

        db_manager.add_student(student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id)
        qr_path = qr_manager.generate_qr(matricula)
        return jsonify({"success": True, "qr_path": qr_path})
    except KeyError as e:
        return jsonify({"success": False, "error": f"Falta el campo {str(e)}"}), 400
    except mysql.connector.Error as e:
        if e.errno == 1062:
            return jsonify({"success": False, "error": f"La matricula {matricula} ya esta registrada"}), 400
        return jsonify({"success": False, "error": "Error en la base de datos"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/upload_excel", methods=["POST"])
@login_required
@role_required("superadmin", "admin_proyecto", api=True)
def upload_excel():
    try:
        if "file" in request.files:
            file = request.files["file"]
        else:
            return jsonify({"success": False, "error": "No se proporciono un archivo Excel"}), 400

        project_id = request.form.get("project_id")
        if not project_id:
            return jsonify({"success": False, "error": "Se requiere project_id"}), 400

        project = db_manager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": "Proyecto no encontrado"}), 400

        import pandas as pd
        df = pd.read_excel(file)

        required_base_columns = ['full_name']
        if not all(col in df.columns for col in required_base_columns):
            return jsonify({"success": False, "error": "El Excel debe contener al menos la columna 'full_name'"}), 400

        project_fields = db_manager.get_project_fields(project_id)
        field_mapping = {field['name']: field['id'] for field in project_fields}

        success_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                full_name = str(row['full_name'])
                email = str(row.get('email', ''))
                if email == 'nan': email = ''
                phone = str(row.get('phone', ''))
                if phone == 'nan': phone = ''

                participant_id = str(uuid.uuid4())
                token = qr_manager.generate_token()

                db_manager.add_participant(participant_id, full_name, email, phone, project_id, token)

                for col in df.columns:
                    if col in field_mapping:
                        val = str(row[col])
                        if val != 'nan':
                            db_manager.add_participant_field_value(participant_id, field_mapping[col], val)

                qr_path = qr_manager.generate_qr(token)
                db_manager.add_credential(participant_id, token, qr_path)
                success_count += 1

            except Exception as e:
                errors.append(f"Error en fila {index}: {str(e)}")

        msg = f"Se procesaron {success_count} participantes correctamente."
        if errors:
            msg += f" Errores: {', '.join(errors)}"
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/send_credentials", methods=["POST"])
@login_required
@role_required("superadmin", "admin_proyecto", api=True)
def send_credentials():
    data = request.get_json()
    participant_id = data.get("participant_id")
    if not participant_id:
        return jsonify({"success": False, "error": "participant_id requerido"}), 400
    db_manager.log_email(participant_id, "enviado")
    return jsonify({"success": True, "message": "Credencial enviada (mock)"})

@app.route("/register_attendance", methods=["POST"])
@login_required
@role_required("superadmin", "admin_proyecto", "staff", api=True)
def register_attendance():
    try:
        data = request.get_json()
        if not data or "qr_data" not in data or "project_id" not in data or "event_type" not in data:
            return jsonify({"success": False, "error": "Faltan datos (qr_data, project_id, event_type)"}), 400

        token = data["qr_data"]
        project_id = data["project_id"]
        event_type = data["event_type"]

        result = attendance_manager.register_attendance(token, project_id, event_type)
        if result == "Asistencia registrada exitosamente":
            return jsonify({"success": True, "message": result})
        return jsonify({"success": False, "error": result}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/get_projects")
@login_required
@role_required("superadmin", "admin_proyecto", "staff", api=True)
def get_projects():
    projects = db_manager.get_all_projects()
    return jsonify([{"id": p[0], "name": p[1]} for p in projects])


@app.route("/export_excel")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def export_excel():
    project_id = request.args.get("project_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    try:
        file_path = db_manager.export_attendance_to_excel(project_id, start_date, end_date)
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/export_pdf")
@login_required
@role_required("superadmin", "admin_proyecto", "staff")
def export_pdf():
    project_id = request.args.get("project_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    try:
        file_path = db_manager.export_attendance_to_pdf(project_id, start_date, end_date)
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/generate_report", methods=["POST"])
@login_required
@role_required("superadmin", "admin_proyecto", "staff", "consulta", api=True)
def generate_report():
    try:
        data = request.form
        start_date = data.get("start_date") or None
        end_date = data.get("end_date") or None
        project_id = data.get("project_id") or None
        format_type = data.get("format")

        report_data = attendance_manager.get_attendance_report(start_date, end_date, project_id)
        if not report_data:
            return jsonify({"success": False, "error": "No hay datos para el reporte"}), 400

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = "static/reports"
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

        if format_type == "pdf":
            report_path = os.path.join(report_dir, f"report_{timestamp}.pdf").replace("\\", "/")
            doc = SimpleDocTemplate(report_path, pagesize=letter)
            elements = []
            styles = getSampleStyleSheet()
            elements.append(Paragraph("Reporte de Asistencias", styles["Title"]))

            table_data = [["Nombre", "Email", "Teléfono", "Proyecto", "Tipo", "Fecha/Hora"]]
            for row in report_data:
                table_data.append(
                    [
                        row['full_name'],
                        row['email'],
                        row['phone'],
                        row['project_name'],
                        row['event_type'],
                        row['local_timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                    ]
                )

            table = Table(table_data)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )
            elements.append(table)
            doc.build(elements)
        elif format_type == "excel":
            report_path = os.path.join(report_dir, f"report_{timestamp}.xlsx").replace("\\", "/")
            workbook = xlsxwriter.Workbook(report_path)
            worksheet = workbook.add_worksheet()
            worksheet.write("A1", "Reporte de Asistencias")
            headers = ["Nombre", "Email", "Teléfono", "Proyecto", "Tipo", "Fecha/Hora"]
            for col, header in enumerate(headers):
                worksheet.write(1, col, header)
            for row_idx, row in enumerate(report_data, 2):
                worksheet.write(row_idx, 0, row['full_name'])
                worksheet.write(row_idx, 1, row['email'])
                worksheet.write(row_idx, 2, row['phone'])
                worksheet.write(row_idx, 3, row['project_name'])
                worksheet.write(row_idx, 4, row['event_type'])
                worksheet.write(row_idx, 5, row['local_timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
            workbook.close()
        else:
            return jsonify({"success": False, "error": "Formato no soportado"}), 400

        return jsonify({"success": True, "report_path": report_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=str_to_bool(os.getenv("FLASK_DEBUG"), default=True),
    )
