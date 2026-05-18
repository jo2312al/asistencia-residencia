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
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash

from attendance_manager import AttendanceManager
from database import DatabaseManager
from qr_manager import QRManager

VALID_ROLES = ("admin", "staff", "guest")
ROLE_LABELS = {
    "admin": "Administrador",
    "staff": "Staff",
    "guest": "Consulta",
}
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
    role_map = {
        "admin": "admin_dashboard",
        "staff": "staff_dashboard",
        "guest": "guest_dashboard",
    }
    return role_map.get(role, "guest_dashboard")


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

db_config = get_db_config()
db_manager = DatabaseManager(db_config)
qr_manager = QRManager()
attendance_manager = AttendanceManager(db_manager)


def ensure_credential_qr(credential):
    token = credential["token"]
    qr_path = credential.get("qr_path") or os.path.join("static/qr_codes", f"{token}.png").replace("\\", "/")
    if not os.path.exists(qr_path):
        qr_path = qr_manager.generate_qr_data(token, token)
    if credential.get("qr_path") != qr_path:
        db_manager.update_credential_qr_path(token, qr_path)
    return qr_path


def ensure_student_qr(student, qr_tool=None):
    active_qr_manager = qr_tool or qr_manager
    matricula = student[4]
    try:
        credential = db_manager.ensure_student_participant_credential(student)
        qr_path = credential.get("qr_path") or os.path.join("static/qr_codes", f"{credential['token']}.png").replace("\\", "/")
        if not os.path.exists(qr_path):
            qr_path = active_qr_manager.generate_qr_data(credential["token"], credential["token"])
        if credential.get("qr_path") != qr_path:
            db_manager.update_credential_qr_path(credential["token"], qr_path)
        return qr_path, credential["token"], False
    except Exception as e:
        app.logger.exception("Falling back to legacy QR for %s: %s", matricula, e)
        qr_path = os.path.join("static/qr_codes", f"{matricula}.png").replace("\\", "/")
        if not os.path.exists(qr_path):
            qr_path = active_qr_manager.generate_qr(matricula)
        return qr_path, None, True


def build_credential_card(student, styles, cell_width, cell_height):
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.webp")
    text_style = ParagraphStyle(
        "CredentialText",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        alignment=1,
    )
    name_style = ParagraphStyle(
        "CredentialName",
        parent=styles["Heading4"],
        fontSize=11,
        leading=13,
        alignment=1,
        textColor=colors.HexColor("#003087"),
    )
    label_style = ParagraphStyle(
        "CredentialLabel",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        alignment=1,
        textColor=colors.HexColor("#6c757d"),
    )

    if os.path.exists(logo_path):
        logo_cell = Image(logo_path, width=0.85 * inch, height=0.85 * inch)
    else:
        logo_cell = Paragraph("<b>Innovatec</b>", name_style)

    token_text = student["credential_token"]
    if student.get("is_legacy_qr"):
        token_text = f"{token_text} / Matricula"

    qr_image = Image(student["qr_path"], width=1.65 * inch, height=1.65 * inch)
    qr_image.hAlign = "CENTER"

    content = [
        [logo_cell],
        [Paragraph("CREDENCIAL DE ACCESO", label_style)],
        [Paragraph(student["name"], name_style)],
        [Paragraph(student["project_name"], text_style)],
        [Paragraph(f"Matricula: {student['matricula']}", text_style)],
        [Paragraph(f"Folio: {token_text}", text_style)],
        [Spacer(1, 0.12 * inch)],
        [qr_image],
        [Paragraph("Presenta este codigo para registrar asistencia", label_style)],
    ]
    card = Table(content, colWidths=[cell_width - 0.25 * inch])
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#f3f7fb")),
                ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#003087")),
                ("LINEBELOW", (0, 1), (-1, 1), 0.8, colors.HexColor("#d9e6f2")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    wrapper = Table([[card]], colWidths=[cell_width], rowHeights=[cell_height])
    wrapper.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return wrapper

default_admin_username = os.getenv("ADMIN_USERNAME")
default_admin_password = os.getenv("ADMIN_PASSWORD")
if default_admin_username and default_admin_password:
    default_hash = generate_password_hash(default_admin_password, method="pbkdf2:sha256")
    db_manager.ensure_user(default_admin_username, default_hash, role="admin")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, username, role="guest"):
        self.id = username
        self.role = role

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_staff(self):
        return self.role == "staff"

    @property
    def is_guest(self):
        return self.role == "guest"


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
    return {"VALID_ROLES": VALID_ROLES, "ROLE_LABELS": ROLE_LABELS}


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
@role_required("admin")
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
@role_required("admin", "staff")
def staff_dashboard():
    projects = db_manager.get_all_projects()
    return render_template("staff_dashboard.html", projects=projects)


@app.route("/guest/dashboard")
@login_required
@role_required("admin", "staff", "guest")
def guest_dashboard():
    return render_template("guest_dashboard.html")


@app.route("/create_user", methods=["GET", "POST"])
@login_required
@role_required("admin")
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
    users = db_manager.get_all_users()
    return render_template("create_user.html", roles=VALID_ROLES, users=users)


@app.route("/users/<path:username>/role", methods=["POST"])
@login_required
@role_required("admin", api=True)
def update_user_role(username):
    try:
        if username == current_user.id:
            return jsonify({"success": False, "error": "No puedes cambiar tu propio rol"}), 400

        role = request.form.get("role")
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            role = payload.get("role", role)

        if not role:
            return jsonify({"success": False, "error": "Rol requerido"}), 400

        updated = db_manager.update_user_role(username, role)
        if not updated:
            return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
        return jsonify({"success": True, "message": "Rol actualizado correctamente"})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/register")
@login_required
@role_required("admin")
def register():
    projects = db_manager.get_all_projects()
    return render_template("register.html", projects=projects)


@app.route("/scan")
@login_required
@role_required("admin", "staff")
def scan():
    return render_template("scan.html")


@app.route("/reports")
@login_required
@role_required("admin", "staff")
def reports():
    projects = db_manager.get_all_projects()
    return render_template("reports.html", projects=projects)


@app.route("/data")
@login_required
@role_required("admin", "staff")
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
        qr_path, credential_token, is_legacy_qr = ensure_student_qr(student)

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
            "credential_token": credential_token or "Legacy",
            "is_legacy_qr": is_legacy_qr,
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
@role_required("admin", "staff")
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
        qr_path, credential_token, is_legacy_qr = ensure_student_qr(student, local_qr_manager)

        project_id = student[6]
        project_name = projects.get(project_id, "Sin proyecto") if project_id else "Sin proyecto"
        project_number = (project_id - 1) if project_id else None

        if os.path.exists(qr_path):
            student_data.append(
                {
                    "name": f"{student[1]} {student[2]} {student[3]}",
                    "matricula": matricula,
                    "credential_token": credential_token or "Legacy",
                    "is_legacy_qr": is_legacy_qr,
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
            grid_data[row][col] = build_credential_card(student, styles, cell_width, cell_height)

        table = Table(grid_data, colWidths=[cell_width, cell_width], rowHeights=[cell_height, cell_height])
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
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
@role_required("admin", "staff")
def download_all_qrs():
    students = db_manager.get_all_students()
    local_qr_manager = QRManager()
    memory_file = BytesIO()

    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for student in students:
            matricula = student[4]
            qr_path, credential_token, _ = ensure_student_qr(student, local_qr_manager)

            if os.path.exists(qr_path):
                qr_name = credential_token or matricula
                zf.write(qr_path, arcname=f"qr_codes/{qr_name}_{matricula}.png")

    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name="all_qr_codes.zip",
    )


@app.route("/generate_qr", methods=["POST"])
@login_required
@role_required("admin", api=True)
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
        student = db_manager.get_student_by_matricula(matricula)
        credential = db_manager.ensure_student_participant_credential(student)
        qr_path = ensure_credential_qr(credential)
        return jsonify({"success": True, "qr_path": qr_path, "credential_token": credential["token"]})
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
@role_required("admin", api=True)
def upload_excel():
    try:
        if "file" in request.files:
            file = request.files["file"]
        elif "excel_file" in request.files:
            file = request.files["excel_file"]
        else:
            return jsonify({"success": False, "error": "No se proporciono un archivo Excel"}), 400

        project_id = request.form.get("project_id") or None
        if file.filename == "":
            return jsonify({"success": False, "error": "No se selecciono un archivo"}), 400
        if project_id:
            conn = mysql.connector.connect(**db_config)
            c = conn.cursor()
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                return jsonify({"success": False, "error": "Proyecto no encontrado"}), 400
            conn.close()

        result = db_manager.upload_students_from_excel(file, project_id)
        return jsonify({"success": True, "message": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/register_attendance", methods=["POST"])
@login_required
@role_required("admin", "staff", api=True)
def register_attendance():
    try:
        data = request.get_json()
        if not data or "qr_data" not in data:
            return jsonify({"success": False, "error": "Datos de QR no proporcionados"}), 400

        qr_data = data["qr_data"]
        result = attendance_manager.register_attendance_by_qr_data(qr_data)
        if result == "Asistencia registrada exitosamente":
            return jsonify({"success": True, "message": result})
        if result == "Este alumno ya fue tomado asistencia":
            return jsonify({"success": False, "error": result})
        return jsonify({"success": False, "error": result}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/get_projects")
@login_required
@role_required("admin", "staff", api=True)
def get_projects():
    projects = db_manager.get_all_projects()
    return jsonify([{"id": p[0], "name": p[1]} for p in projects])


@app.route("/export_excel")
@login_required
@role_required("admin", "staff")
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
@role_required("admin", "staff")
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
@role_required("admin", "staff", api=True)
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
            elements.append(Paragraph("Reporte de Asistencias - Innovatec TecNM", styles["Title"]))

            table_data = [["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Fecha/Hora"]]
            for row in report_data:
                table_data.append(
                    [
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5] or "Sin proyecto",
                        row[6].strftime("%Y-%m-%d %H:%M:%S"),
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
                        ("FONTSIZE", (0, 0), (-1, 0), 12),
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
            worksheet.write("A1", "Reporte de Asistencias - Innovatec TecNM")
            headers = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Fecha/Hora"]
            for col, header in enumerate(headers):
                worksheet.write(1, col, header)
            for row_idx, row in enumerate(report_data, 2):
                worksheet.write(row_idx, 0, row[0])
                worksheet.write(row_idx, 1, row[1])
                worksheet.write(row_idx, 2, row[2])
                worksheet.write(row_idx, 3, row[3])
                worksheet.write(row_idx, 4, row[4])
                worksheet.write(row_idx, 5, row[5] or "Sin proyecto")
                worksheet.write(row_idx, 6, row[6].strftime("%Y-%m-%d %H:%M:%S"))
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
