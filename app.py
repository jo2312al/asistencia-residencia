from io import BytesIO
import os
import smtplib
import unicodedata
import uuid
import zipfile
from datetime import datetime
from email.message import EmailMessage
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

BASE_REGISTRATION_FIELD_NAMES = {"nombre", "apellido paterno", "apellido materno", "matricula", "carrera"}


def normalize_field_name(value):
    text = unicodedata.normalize("NFD", value or "")
    return "".join(char for char in text if unicodedata.category(char) != "Mn").strip().lower()


def get_mail_config():
    server = os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST")
    username = os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USER")
    password = os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    sender = os.getenv("MAIL_DEFAULT_SENDER") or username
    return {
        "server": server,
        "port": int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587"))),
        "username": username,
        "password": password,
        "sender": sender,
        "use_tls": str_to_bool(os.getenv("MAIL_USE_TLS", "true"), default=True),
        "use_ssl": str_to_bool(os.getenv("MAIL_USE_SSL", "false"), default=False),
    }


def send_mail(recipient, subject, body, attachments=None):
    config = get_mail_config()
    missing = [key for key in ("server", "username", "password", "sender") if not config.get(key)]
    if missing:
        raise RuntimeError("Configura SMTP en Azure: MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD y MAIL_DEFAULT_SENDER")

    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    for path, filename in attachments or []:
        with open(path, "rb") as attachment:
            message.add_attachment(
                attachment.read(),
                maintype="image",
                subtype="png",
                filename=filename,
            )

    smtp_cls = smtplib.SMTP_SSL if config["use_ssl"] else smtplib.SMTP
    with smtp_cls(config["server"], config["port"], timeout=30) as smtp:
        if config["use_tls"] and not config["use_ssl"]:
            smtp.starttls()
        smtp.login(config["username"], config["password"])
        smtp.send_message(message)


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

    qr_image = Image(student["qr_path"], width=1.65 * inch, height=1.65 * inch)
    qr_image.hAlign = "CENTER"

    content = [
        [logo_cell],
        [Paragraph("CREDENCIAL DE ACCESO", label_style)],
        [Paragraph(student["name"], name_style)],
        [Paragraph(student["project_name"], text_style)],
        [Paragraph(f"Matricula: {student['matricula']}", text_style)],
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


def build_rectangular_credential_card(student, styles, card_width, card_height):
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.webp")
    text_style = ParagraphStyle(
        "RectCredentialText",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#212529"),
    )
    name_style = ParagraphStyle(
        "RectCredentialName",
        parent=styles["Heading4"],
        fontSize=13,
        leading=15,
        textColor=colors.HexColor("#003087"),
    )
    label_style = ParagraphStyle(
        "RectCredentialLabel",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#6c757d"),
    )

    if os.path.exists(logo_path):
        logo_cell = Image(logo_path, width=0.75 * inch, height=0.75 * inch)
    else:
        logo_cell = Paragraph("<b>Innovatec</b>", name_style)

    qr_image = Image(student["qr_path"], width=1.25 * inch, height=1.25 * inch)
    info = Table(
        [
            [Paragraph("CREDENCIAL DE ACCESO", label_style)],
            [Paragraph(student["name"], name_style)],
            [Paragraph(student["project_name"], text_style)],
            [Paragraph(f"Matricula: {student['matricula']}", text_style)],
            [Paragraph("Presenta este codigo para registrar asistencia", label_style)],
        ],
        colWidths=[card_width - 1.85 * inch],
    )
    info.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    card = Table(
        [[logo_cell, info, qr_image]],
        colWidths=[0.65 * inch, card_width - 1.85 * inch, 1.2 * inch],
        rowHeights=[card_height],
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f7fb")),
                ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#003087")),
                ("LINEAFTER", (0, 0), (0, -1), 0.8, colors.HexColor("#d9e6f2")),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return card


def get_credential_students(qr_tool=None, event_id=None):
    students = db_manager.get_all_students_filtered(event_id_filter=event_id) if event_id else db_manager.get_all_students()
    local_qr_manager = qr_tool or QRManager()
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
    return student_data

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


def normalize_datetime_input(value):
    if not value:
        return None
    return value.replace("T", " ")


@app.route("/events")
@login_required
@role_required("admin")
def events():
    return render_template(
        "events.html",
        events=db_manager.get_all_events(),
        projects=db_manager.get_all_projects(),
    )


@app.route("/events", methods=["POST"])
@login_required
@role_required("admin")
def create_event():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    start_datetime = normalize_datetime_input(request.form.get("start_datetime"))
    end_datetime = normalize_datetime_input(request.form.get("end_datetime"))
    location = (request.form.get("location") or "").strip() or None
    status = request.form.get("status") or "active"
    event_type = request.form.get("event_type") or "general"
    duplicate_policy = request.form.get("duplicate_policy") or "once_per_day"
    selected_fields = request.form.getlist("event_fields")
    custom_field = (request.form.get("custom_field") or "").strip()

    if not name:
        flash("Nombre del evento requerido", "danger")
        return redirect(url_for("events"))

    try:
        event_id = db_manager.add_event(
            name,
            description,
            start_datetime,
            end_datetime,
            location,
            status,
            event_type,
            duplicate_policy,
        )
        field_options = {
            "email": ("Correo", "email", True),
            "telefono": ("Telefono", "tel", False),
            "equipo": ("Equipo", "text", False),
            "categoria": ("Categoria", "text", False),
            "institucion": ("Institucion", "text", False),
            "rfc": ("RFC", "text", False),
        }
        for order, field_key in enumerate(selected_fields, start=1):
            if field_key in field_options:
                field_name, field_type, is_required = field_options[field_key]
                db_manager.add_event_field(event_id, field_name, field_type, is_required, order)
        if custom_field:
            db_manager.add_event_field(event_id, custom_field, "text", False, len(selected_fields) + 1)
        flash("Evento creado correctamente", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("events"))


@app.route("/projects", methods=["POST"])
@login_required
@role_required("admin")
def create_project():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    event_id = request.form.get("event_id", type=int)

    if not name:
        flash("Nombre del proyecto requerido", "danger")
        return redirect(url_for("events"))

    try:
        db_manager.add_project(name, description, event_id)
        flash("Proyecto agregado correctamente", "success")
    except mysql.connector.Error as e:
        if e.errno == 1062:
            flash("Ya existe un proyecto con ese nombre", "warning")
        else:
            flash("Error al crear proyecto", "danger")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("events"))


@app.route("/register")
@login_required
@role_required("admin")
def register():
    events = db_manager.get_active_events()
    return render_template("register.html", events=events)


@app.route("/settings")
@login_required
@role_required("admin")
def settings():
    projects = db_manager.get_all_projects()
    selected_project_id = request.args.get("project_id", type=int)
    selected_project = None
    project_fields = []

    if selected_project_id:
        for project in projects:
            if project[0] == selected_project_id:
                selected_project = project
                break
        if selected_project:
            project_fields = db_manager.get_project_fields(selected_project_id)
        else:
            flash("Proyecto no encontrado", "warning")

    return render_template(
        "settings.html",
        projects=projects,
        selected_project=selected_project,
        selected_project_id=selected_project_id,
        project_fields=project_fields,
    )


@app.route("/settings/project-fields", methods=["POST"])
@login_required
@role_required("admin")
def create_project_field():
    project_id = request.form.get("project_id", type=int)
    name = (request.form.get("name") or "").strip()
    field_type = request.form.get("field_type") or "text"
    is_required = request.form.get("is_required") == "on"
    display_order = request.form.get("display_order", 0, type=int)

    if not project_id or not name:
        flash("Proyecto y nombre del campo son requeridos", "danger")
        return redirect(url_for("settings", project_id=project_id or ""))

    try:
        db_manager.add_project_field(project_id, name, field_type, is_required, display_order)
        flash("Campo agregado correctamente", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", project_id=project_id))


@app.route("/settings/project-fields/<int:field_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_project_field(field_id):
    project_id = request.form.get("project_id", type=int)
    try:
        deleted = db_manager.delete_project_field(field_id)
        flash("Campo eliminado" if deleted else "Campo no encontrado", "success" if deleted else "warning")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", project_id=project_id or ""))


@app.route("/project_fields/<int:project_id>")
@login_required
@role_required("admin", api=True)
def project_fields_api(project_id):
    if not db_manager.get_project(project_id):
        return jsonify({"success": False, "error": "Proyecto no encontrado"}), 404

    return jsonify({
        "success": True,
        "fields": db_manager.get_project_fields_as_dicts(project_id),
    })


@app.route("/event_fields/<int:event_id>")
@login_required
@role_required("admin", api=True)
def event_fields_api(event_id):
    if not db_manager.get_event(event_id):
        return jsonify({"success": False, "error": "Evento no encontrado"}), 404

    fields = [
        field
        for field in db_manager.get_event_fields_as_dicts(event_id)
        if normalize_field_name(field["name"]) not in BASE_REGISTRATION_FIELD_NAMES
    ]
    return jsonify({
        "success": True,
        "fields": fields,
    })


@app.route("/event_projects/<int:event_id>")
@login_required
@role_required("admin", "staff", api=True)
def event_projects_api(event_id):
    if not db_manager.get_event(event_id):
        return jsonify({"success": False, "error": "Evento no encontrado"}), 404

    projects = db_manager.get_projects_by_event(event_id)
    return jsonify({
        "success": True,
        "projects": [{"id": p[0], "name": p[1]} for p in projects],
    })


@app.route("/scan")
@login_required
@role_required("admin", "staff")
def scan():
    return render_template("scan.html", events=db_manager.get_active_events())


@app.route("/reports")
@login_required
@role_required("admin", "staff")
def reports():
    projects = db_manager.get_all_projects()
    events = db_manager.get_all_events()
    return render_template("reports.html", projects=projects, events=events)


@app.route("/data")
@login_required
@role_required("admin", "staff")
def data():
    matricula_search = request.args.get("matricula", "").strip()
    apellido_p_search = request.args.get("apellido_p", "").strip()
    project_id_filter = request.args.get("project_id", "")
    event_id_filter = request.args.get("event_id", "")

    page = request.args.get("page", 1, type=int)
    per_page = 10

    projects = db_manager.get_all_projects()
    events = db_manager.get_all_events()
    all_students = db_manager.get_all_students_filtered(
        matricula_search,
        apellido_p_search,
        project_id_filter,
        event_id_filter,
    )

    total_students = len(all_students)
    total_pages = (total_students + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    students_paginated = all_students[start:end]
    custom_values_by_student = db_manager.get_field_values_by_student_ids([student[0] for student in students_paginated])

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
            "event_id": student[7] if len(student) > 7 else None,
            "participant_type": student[8] if len(student) > 8 else "alumno",
            "project_name": project_name,
            "credential_token": credential_token or "Legacy",
            "is_legacy_qr": is_legacy_qr,
            "qr_path": qr_path if os.path.exists(qr_path) else None,
            "custom_fields": custom_values_by_student.get(student[0], []),
        }
        students_with_qr.append(student_dict)

    return render_template(
        "data.html",
        students=students_with_qr,
        projects=projects,
        events=events,
        page=page,
        total_pages=total_pages,
        matricula_search=matricula_search,
        apellido_p_search=apellido_p_search,
        project_id_filter=project_id_filter,
        event_id_filter=event_id_filter,
    )


@app.route("/download_all_qrs_pdf")
@login_required
@role_required("admin", "staff")
def download_all_qrs_pdf():
    local_qr_manager = QRManager()
    event_id = request.args.get("event_id") or None

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

    student_data = get_credential_students(local_qr_manager, event_id)

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
    return send_file(pdf_path, as_attachment=True, download_name="credenciales.pdf")


@app.route("/download_all_credentials_rect_pdf")
@login_required
@role_required("admin", "staff")
def download_all_credentials_rect_pdf():
    local_qr_manager = QRManager()
    event_id = request.args.get("event_id") or None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = "static/reports"
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    pdf_path = os.path.join(report_dir, f"credentials_rect_{timestamp}.pdf").replace("\\", "/")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()
    student_data = get_credential_students(local_qr_manager, event_id)

    page_width = letter[0]
    page_height = letter[1]
    margin = 0.45 * inch
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin
    card_width = usable_width / 2
    card_height = 1.55 * inch

    for i in range(0, len(student_data), 10):
        chunk = student_data[i:i + 10]
        rows = []
        for j in range(0, len(chunk), 2):
            left = build_rectangular_credential_card(chunk[j], styles, card_width - 0.08 * inch, card_height)
            right = build_rectangular_credential_card(chunk[j + 1], styles, card_width - 0.08 * inch, card_height) if j + 1 < len(chunk) else ""
            rows.append([left, right])

        table = Table(rows, colWidths=[card_width, card_width], rowHeights=[usable_height / 5] * len(rows))
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(table)
        if i + 10 < len(student_data):
            elements.append(Spacer(1, 0.2 * inch))

    doc.build(elements)
    return send_file(pdf_path, as_attachment=True, download_name="credenciales_rectangulares.pdf")


@app.route("/download_all_qrs")
@login_required
@role_required("admin", "staff")
def download_all_qrs():
    event_id = request.args.get("event_id") or None
    students = db_manager.get_all_students_filtered(event_id_filter=event_id) if event_id else db_manager.get_all_students()
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


def build_credential_email_batches(event_id):
    rows = db_manager.get_event_credential_rows(event_id)
    projects_with_advisors = {}
    for row in rows:
        participant_type = (row.get("participant_type") or "").lower()
        if participant_type == "asesor" and row.get("email") and row.get("project_id"):
            projects_with_advisors.setdefault(row["project_id"], []).append(row["email"])

    batches = []
    grouped_by_project = {}
    for row in rows:
        grouped_by_project.setdefault(row.get("project_id"), []).append(row)

    for project_id, project_rows in grouped_by_project.items():
        advisor_emails = projects_with_advisors.get(project_id, [])
        if advisor_emails:
            for email in advisor_emails:
                batches.append({"recipient": email, "rows": project_rows, "grouped": True})
        else:
            for row in project_rows:
                if row.get("email"):
                    batches.append({"recipient": row["email"], "rows": [row], "grouped": False})
    return batches


@app.route("/send_event_credentials", methods=["POST"])
@login_required
@role_required("admin")
def send_event_credentials():
    event_id = request.form.get("event_id") or None
    if not event_id:
        flash("Selecciona un evento para enviar credenciales", "warning")
        return redirect(url_for("data"))

    event = db_manager.get_event(event_id)
    if not event:
        flash("Evento no encontrado", "danger")
        return redirect(url_for("data"))

    batches = build_credential_email_batches(event_id)
    if not batches:
        flash("No hay correos disponibles para enviar credenciales", "warning")
        return redirect(url_for("data", event_id=event_id))

    sent_count = 0
    error_count = 0
    subject = f"Credenciales para {event[1]}"
    for batch in batches:
        attachments = []
        credential_ids = []
        for row in batch["rows"]:
            qr_path = ensure_credential_qr({"token": row["token"], "qr_path": row.get("qr_path")})
            if os.path.exists(qr_path):
                filename = f"{row.get('matricula') or row['token']}.png"
                attachments.append((qr_path, filename))
                credential_ids.append(row["credential_id"])

        names = "\n".join(f"- {row['full_name']}" for row in batch["rows"])
        body = (
            f"Hola,\n\nAdjuntamos las credenciales para {event[1]}.\n\n"
            f"Participantes:\n{names}\n\n"
            "Presenten el QR al momento del registro de asistencia.\n"
        )
        try:
            send_mail(batch["recipient"], subject, body, attachments)
            db_manager.update_credentials_sent_status(credential_ids, "sent")
            db_manager.log_email(event_id, batch["recipient"], subject, "sent")
            sent_count += 1
        except Exception as exc:
            db_manager.update_credentials_sent_status(credential_ids, "error")
            db_manager.log_email(event_id, batch["recipient"], subject, "error", str(exc))
            error_count += 1

    flash(f"Envios completados: {sent_count}. Errores: {error_count}.", "success" if error_count == 0 else "warning")
    return redirect(url_for("data", event_id=event_id))


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
        event_id = data.get("event_id") or None
        project_id = data.get("project_id") or None
        participant_type = data.get("participant_type") or "alumno"

        if not event_id:
            return jsonify({"success": False, "error": "Selecciona un evento"}), 400

        if event_id and not db_manager.get_event(event_id):
            return jsonify({"success": False, "error": "Evento no encontrado"}), 400

        if project_id:
            valid_project_ids = {str(project[0]) for project in db_manager.get_projects_by_event(event_id)}
            if str(project_id) not in valid_project_ids:
                return jsonify({"success": False, "error": f"Proyecto con ID {project_id} no existe"}), 400

        dynamic_values = {}
        email_value = None
        if event_id:
            for field in db_manager.get_event_fields(event_id):
                field_id = field[0]
                field_name = field[2]
                if normalize_field_name(field_name) in BASE_REGISTRATION_FIELD_NAMES:
                    continue
                is_required = bool(field[4])
                value = (data.get(f"field_{field_id}") or "").strip()
                if is_required and not value:
                    return jsonify({"success": False, "error": f"Falta el campo {field_name}"}), 400
                if value:
                    dynamic_values[field_id] = value
                if normalize_field_name(field_name) in ("correo", "email") and value:
                    email_value = value

        db_manager.add_student(
            student_id,
            first_name,
            last_name_p,
            last_name_m,
            matricula,
            carrera,
            project_id,
            event_id,
            email_value,
            participant_type,
        )
        student = db_manager.get_student_by_matricula(matricula)
        credential = db_manager.ensure_student_participant_credential(student)
        participant_id = db_manager.get_participant_id_by_student_id(student[0])
        db_manager.save_participant_event_field_values(participant_id, dynamic_values)
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
        event_id = request.form.get("event_id") or None
        if file.filename == "":
            return jsonify({"success": False, "error": "No se selecciono un archivo"}), 400
        if not event_id:
            return jsonify({"success": False, "error": "Selecciona un evento"}), 400
        if not db_manager.get_event(event_id):
            return jsonify({"success": False, "error": "Evento no encontrado"}), 400
        if project_id:
            valid_project_ids = {str(project[0]) for project in db_manager.get_projects_by_event(event_id)}
            if str(project_id) not in valid_project_ids:
                return jsonify({"success": False, "error": "Proyecto no encontrado para este evento"}), 400

        result = db_manager.upload_students_from_excel(file, project_id, event_id)
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
        event_id = data.get("event_id")
        try:
            event_id = int(event_id) if event_id else None
        except (TypeError, ValueError):
            event_id = None
        result = attendance_manager.register_attendance_by_qr_data(qr_data, event_id)
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
        event_id = data.get("event_id") or None
        format_type = data.get("format")

        if event_id:
            if format_type == "pdf":
                report_path = db_manager.export_event_attendance_to_pdf(event_id, project_id, start_date, end_date)
            elif format_type == "excel":
                report_path = db_manager.export_event_attendance_to_excel(event_id, project_id, start_date, end_date)
            else:
                return jsonify({"success": False, "error": "Formato no soportado"}), 400
            return jsonify({"success": True, "report_path": report_path})

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
