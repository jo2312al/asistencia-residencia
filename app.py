from io import BytesIO
import os
import smtplib
import unicodedata
import uuid
import zipfile
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
from time import monotonic

import mysql.connector
import pandas as pd
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
DATA_CACHE_TTL_SECONDS = int(os.getenv("DATA_CACHE_TTL_SECONDS", "30"))
DATA_METADATA_CACHE = {}

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


def send_registered_credential_silently(event_id, project_id, participant_type, recipient_email, credential, qr_path, full_name, matricula):
    if not event_id:
        return

    try:
        event = db_manager.get_event(event_id)
        if not event:
            return

        recipient = recipient_email
        if project_id and (participant_type or "").lower() != "asesor":
            for row in db_manager.get_event_credential_rows(event_id):
                if row.get("project_id") == int(project_id) and (row.get("participant_type") or "").lower() == "asesor" and row.get("email"):
                    recipient = row["email"]
                    break

        if not recipient:
            return

        subject = f"Credencial para {event[1]}"
        body = (
            f"Hola,\n\nSe registro la credencial de {full_name} para {event[1]}.\n\n"
            f"Matricula: {matricula}\n\n"
            "Adjuntamos el QR para presentarlo en el registro de asistencia.\n"
        )
        filename = f"{matricula or credential['token']}.png"
        send_mail(recipient, subject, body, [(qr_path, filename)])
        db_manager.update_credentials_sent_status([credential["id"]], "sent")
        db_manager.log_email(event_id, recipient, subject, "sent")
    except Exception as exc:
        try:
            db_manager.update_credentials_sent_status([credential["id"]], "error")
            db_manager.log_email(event_id, recipient_email or "sin destinatario", "Credencial", "error", str(exc))
        except Exception:
            pass


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


def ensure_row_qr(row, qr_tool=None):
    token = row.get("credential_token")
    if token:
        qr_path = row.get("qr_path") or os.path.join("static/qr_codes", f"{token}.png").replace("\\", "/")
        if not os.path.exists(qr_path):
            qr_path = (qr_tool or qr_manager).generate_qr_data(token, token)
        if row.get("qr_path") != qr_path:
            db_manager.update_credential_qr_path(token, qr_path)
        return qr_path, token, False

    student = (
        row["id"],
        row["first_name"],
        row["last_name_p"],
        row["last_name_m"],
        row["matricula"],
        row["carrera"],
        row.get("project_id"),
        row.get("event_id"),
        row.get("participant_type") or "alumno",
    )
    return ensure_student_qr(student, qr_tool)


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
        logo_cell = Paragraph("<b>AsisTec</b>", name_style)

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
        logo_cell = Paragraph("<b>AsisTec</b>", name_style)

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
    local_qr_manager = qr_tool or QRManager()
    student_data = []
    for row in db_manager.get_students_for_credentials(event_id):
        matricula = row["matricula"]
        qr_path, credential_token, is_legacy_qr = ensure_row_qr(row, local_qr_manager)
        project_id = row.get("project_id")
        project_name = row.get("project_name") or "Sin proyecto"
        project_number = (project_id - 1) if project_id else None

        if os.path.exists(qr_path):
            student_data.append(
                {
                    "name": f"{row['first_name']} {row['last_name_p']} {row['last_name_m']}",
                    "matricula": matricula,
                    "credential_token": credential_token or "Legacy",
                    "is_legacy_qr": is_legacy_qr,
                    "qr_path": qr_path,
                    "project_id": project_id,
                    "project_name": project_name,
                    "project_number": project_number,
                }
            )

    return student_data


def safe_filename(value, fallback="archivo"):
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = "".join(char if char.isalnum() else "_" for char in text.lower())
    text = "_".join(part for part in text.split("_") if part)
    return text or fallback


def ensure_report_dir():
    report_dir = "static/reports"
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    return report_dir


def build_standard_credentials_pdf(student_data, output):
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    page_width = letter[0]
    page_height = letter[1]
    margin = 0.5 * inch
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin
    cell_width = usable_width / 2
    cell_height = usable_height / 2.5

    for i in range(0, len(student_data), 4):
        students_chunk = student_data[i:i + 4]
        grid_data = [["", ""], ["", ""]]
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


def build_rectangular_credentials_pdf(student_data, output):
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

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
    return {
        "VALID_ROLES": VALID_ROLES,
        "ROLE_LABELS": ROLE_LABELS,
        "datetime_local_value": datetime_local_value,
    }


def render_text_template(template, **values):
    text = template or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value or ""))
    return text


def datetime_local_value(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%dT%H:%M")
    return str(value).replace(" ", "T")[:16]


def filter_events_for_current_user(events):
    if not current_user.is_authenticated or current_user.role == "admin":
        return events
    allowed = db_manager.get_user_event_permissions(current_user.id)
    if not allowed:
        return events
    allowed_set = set(allowed)
    return [event for event in events if event[0] in allowed_set]


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
    latest_event = db_manager.get_latest_event()
    latest_event_id = latest_event[0] if latest_event else None
    projects = db_manager.get_projects_by_event(latest_event_id) if latest_event_id else []
    return render_template(
        "admin_dashboard.html",
        projects=projects,
        latest_event=latest_event,
        total_students=db_manager.get_total_students(latest_event_id),
        total_attendance=db_manager.get_total_attendance(latest_event_id),
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
    event_permissions = {user[0]: db_manager.get_user_event_permissions(user[0]) for user in users}
    return render_template(
        "create_user.html",
        roles=VALID_ROLES,
        users=users,
        events=db_manager.get_all_events(),
        event_permissions=event_permissions,
    )


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


@app.route("/users/<path:username>/events", methods=["POST"])
@login_required
@role_required("admin")
def update_user_events(username):
    if not db_manager.get_user(username):
        flash("Usuario no encontrado", "warning")
        return redirect(url_for("create_user"))
    event_ids = [int(event_id) for event_id in request.form.getlist("event_ids") if event_id.isdigit()]
    try:
        db_manager.set_user_event_permissions(username, event_ids)
        flash("Permisos por evento actualizados", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("create_user"))


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


@app.route("/events/<int:event_id>")
@login_required
@role_required("admin")
def event_detail(event_id):
    event = db_manager.get_event(event_id)
    if not event:
        flash("Evento no encontrado", "warning")
        return redirect(url_for("events" if current_user.role == "admin" else "staff_dashboard"))

    participants = db_manager.get_all_students_filtered(event_id_filter=event_id)
    custom_values = db_manager.get_field_values_by_student_ids([student[0] for student in participants])
    projects = db_manager.get_projects_by_event(event_id)
    project_names = {project[0]: project[1] for project in projects}
    participant_rows = []
    for student in participants:
        detail = db_manager.get_student_by_id(student[0]) or {}
        participant_rows.append({
            "id": student[0],
            "first_name": student[1],
            "last_name_p": student[2],
            "last_name_m": student[3],
            "matricula": student[4],
            "carrera": student[5],
            "project_id": student[6],
            "event_id": student[7],
            "participant_type": student[8],
            "project_name": project_names.get(student[6], "Sin proyecto"),
            "email": detail.get("email") or "",
            "custom_fields": custom_values.get(student[0], []),
        })

    try:
        counts = db_manager.get_event_counts(event_id)
    except Exception:
        counts = {
            "participants": len(participant_rows),
            "projects": len(projects),
            "attendance": 0,
            "credentials": 0,
        }
    try:
        email_logs = db_manager.get_event_email_logs(event_id)
    except Exception:
        email_logs = []
    try:
        attendance_events = db_manager.get_event_attendance_events(event_id)
    except Exception:
        attendance_events = []

    return render_template(
        "event_detail.html",
        event=event,
        projects=projects,
        participants=participant_rows,
        counts=counts,
        email_logs=email_logs,
        attendance_events=attendance_events,
    )


@app.route("/events/<int:event_id>/edit", methods=["POST"])
@login_required
@role_required("admin")
def update_event(event_id):
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Nombre del evento requerido", "danger")
        return redirect(url_for("event_detail", event_id=event_id))

    try:
        updated = db_manager.update_event(
            event_id,
            name,
            (request.form.get("description") or "").strip() or None,
            normalize_datetime_input(request.form.get("start_datetime")),
            normalize_datetime_input(request.form.get("end_datetime")),
            (request.form.get("location") or "").strip() or None,
            request.form.get("status") or "active",
            request.form.get("event_type") or "general",
            request.form.get("duplicate_policy") or "once_per_day",
        )
        flash("Evento actualizado" if updated else "Evento sin cambios", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("event_detail", event_id=event_id))


@app.route("/participants/<path:student_id>/edit", methods=["POST"])
@login_required
@role_required("admin")
def update_participant(student_id):
    student = db_manager.get_student_by_id(student_id)
    if not student:
        flash("Participante no encontrado", "warning")
        return redirect(url_for("data"))

    event_id = student.get("event_id")
    project_id = request.form.get("project_id") or None
    try:
        if project_id:
            valid_project_ids = {str(project[0]) for project in db_manager.get_projects_by_event(event_id)}
            if str(project_id) not in valid_project_ids:
                flash("Proyecto no pertenece al evento", "danger")
                return redirect(url_for("event_detail", event_id=event_id))

        db_manager.update_student_participant(
            student_id,
            (request.form.get("first_name") or "").strip(),
            (request.form.get("last_name_p") or "").strip(),
            (request.form.get("last_name_m") or "").strip(),
            (request.form.get("matricula") or "").strip(),
            (request.form.get("carrera") or "").strip(),
            project_id,
            (request.form.get("email") or "").strip() or None,
            request.form.get("participant_type") or "alumno",
        )
        flash("Participante actualizado", "success")
    except mysql.connector.Error as e:
        if e.errno == 1062:
            flash("La matricula ya existe en otro participante", "danger")
        else:
            flash("Error al actualizar participante", "danger")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("event_detail", event_id=event_id))


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
    events = db_manager.get_all_events()
    selected_event_id = request.args.get("event_id", type=int)
    selected_event = None
    event_fields = []

    if selected_event_id:
        selected_event = db_manager.get_event(selected_event_id)
        if selected_event:
            event_fields = db_manager.get_event_fields(selected_event_id)
        else:
            flash("Evento no encontrado", "warning")

    mail_config = get_mail_config()
    mail_status = {
        "server": mail_config.get("server") or "",
        "port": mail_config.get("port"),
        "username": mail_config.get("username") or "",
        "sender": mail_config.get("sender") or "",
        "use_tls": mail_config.get("use_tls"),
        "use_ssl": mail_config.get("use_ssl"),
        "is_ready": not [key for key in ("server", "username", "password", "sender") if not mail_config.get(key)],
    }

    return render_template(
        "settings.html",
        events=events,
        selected_event=selected_event,
        selected_event_id=selected_event_id,
        event_fields=event_fields,
        event_template=db_manager.get_event_template(selected_event_id) if selected_event else None,
        mail_status=mail_status,
        system_info={
            "app_name": "AsisTec",
            "database": db_config.get("database"),
            "environment": os.getenv("FLASK_ENV") or os.getenv("ENVIRONMENT") or "production",
        },
    )


@app.route("/settings/event-fields", methods=["POST"])
@login_required
@role_required("admin")
def create_event_field():
    event_id = request.form.get("event_id", type=int)
    name = (request.form.get("name") or "").strip()
    field_type = request.form.get("field_type") or "text"
    is_required = request.form.get("is_required") == "on"
    display_order = request.form.get("display_order", 0, type=int)

    if not event_id or not name:
        flash("Evento y nombre del campo son requeridos", "danger")
        return redirect(url_for("settings", event_id=event_id or ""))

    try:
        db_manager.add_event_field(event_id, name, field_type, is_required, display_order)
        flash("Campo agregado correctamente", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", event_id=event_id))


@app.route("/settings/event-fields/<int:field_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_event_field(field_id):
    event_id = request.form.get("event_id", type=int)
    try:
        deleted = db_manager.delete_event_field(field_id)
        flash("Campo eliminado" if deleted else "Campo no encontrado", "success" if deleted else "warning")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", event_id=event_id or ""))


@app.route("/settings/event-rules", methods=["POST"])
@login_required
@role_required("admin")
def update_event_rules():
    event_id = request.form.get("event_id", type=int)
    duplicate_policy = request.form.get("duplicate_policy") or "once_per_day"
    status = request.form.get("status") or None

    if not event_id:
        flash("Selecciona un evento para actualizar reglas", "danger")
        return redirect(url_for("settings"))

    try:
        updated = db_manager.update_event_rules(event_id, duplicate_policy, status)
        flash("Reglas actualizadas" if updated else "Evento no encontrado", "success" if updated else "warning")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", event_id=event_id))


@app.route("/settings/event-template", methods=["POST"])
@login_required
@role_required("admin")
def update_event_template():
    event_id = request.form.get("event_id", type=int)
    if not event_id or not db_manager.get_event(event_id):
        flash("Selecciona un evento valido para guardar plantilla", "danger")
        return redirect(url_for("settings"))
    try:
        db_manager.save_event_template(
            event_id,
            (request.form.get("email_subject") or "").strip() or "Credenciales para {event_name}",
            request.form.get("email_body") or "",
            request.form.get("credential_style") or "standard",
            (request.form.get("logo_filename") or "").strip() or None,
        )
        flash("Plantilla actualizada", "success")
    except Exception as e:
        flash(str(e), "danger")
    return redirect(url_for("settings", event_id=event_id))


@app.route("/settings/project-fields", methods=["POST"])
@login_required
@role_required("admin")
def create_project_field():
    project_id = request.form.get("project_id", type=int)
    flash("Los campos ahora se administran por evento.", "warning")
    return redirect(url_for("settings"))


@app.route("/settings/project-fields/<int:field_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_project_field(field_id):
    flash("Los campos ahora se administran por evento.", "warning")
    return redirect(url_for("settings"))


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
    return render_template("scan.html", events=filter_events_for_current_user(db_manager.get_active_events()))


@app.route("/reports")
@login_required
@role_required("admin", "staff")
def reports():
    projects = db_manager.get_all_projects()
    events = filter_events_for_current_user(db_manager.get_all_events())
    return render_template("reports.html", projects=projects, events=events)


@app.route("/data")
@login_required
@role_required("admin", "staff")
def data():
    filters = get_participant_filters()
    page_data = get_participant_page(filters)
    context = build_data_context(filters, page_data)
    return render_template("data.html", **context)


def get_participant_filters():
    return {
        "matricula_search": request.args.get("matricula", "").strip(),
        "apellido_p_search": request.args.get("apellido_p", "").strip(),
        "project_id_filter": request.args.get("project_id", ""),
        "event_id_filter": request.args.get("event_id", ""),
        "sort_by": valid_sort(request.args.get("sort", "proyecto")),
        "sort_dir": valid_sort_dir(request.args.get("dir", "asc")),
        "show_details": request.args.get("details") == "1",
        "page": request.args.get("page", 1, type=int),
        "per_page": 10,
    }


def valid_sort(sort_by):
    allowed = {"matricula", "nombre", "apellido_p", "apellido_m", "carrera", "tipo", "proyecto"}
    return sort_by if sort_by in allowed else "proyecto"


def valid_sort_dir(sort_dir):
    return sort_dir if sort_dir in {"asc", "desc"} else "asc"


def get_participant_page(filters):
    page = max(1, filters["page"])
    rows, total = fetch_filtered_students_page(filters, page)
    if not rows and page > 1:
        total = count_filtered_students(filters)
        page = max(1, min(page, max((total + filters["per_page"] - 1) // filters["per_page"], 1)))
        rows, total = fetch_filtered_students_page(filters, page)
    total_pages = max((total + filters["per_page"] - 1) // filters["per_page"], 1)
    return {"rows": rows, "page": page, "total_pages": total_pages}


def count_filtered_students(filters):
    if not filters["event_id_filter"]:
        return 0
    return db_manager.count_students_filtered(*student_filter_args(filters))


def student_filter_args(filters):
    return (
        filters["matricula_search"],
        filters["apellido_p_search"],
        filters["project_id_filter"],
        filters["event_id_filter"],
    )


def fetch_filtered_students(filters, page):
    if not filters["event_id_filter"]:
        return []
    offset = (page - 1) * filters["per_page"]
    return db_manager.get_all_students_filtered(
        *student_filter_args(filters),
        filters["sort_by"],
        filters["sort_dir"],
        filters["per_page"],
        offset,
    )


def fetch_filtered_students_page(filters, page):
    if not filters["event_id_filter"]:
        return [], 0
    offset = (page - 1) * filters["per_page"]
    return db_manager.get_students_filtered_page(
        *student_filter_args(filters),
        filters["sort_by"],
        filters["sort_dir"],
        filters["per_page"],
        offset,
    )


def build_data_context(filters, page_data):
    projects = cached_projects_by_event(filters["event_id_filter"]) if filters["event_id_filter"] else []
    students = build_student_rows(page_data["rows"], projects, filters["show_details"])
    return {
        "students": students,
        "projects": projects,
        "events": filter_events_for_current_user(cached_all_events()),
        "page": page_data["page"],
        "total_pages": page_data["total_pages"],
        "selected_event": cached_event(filters["event_id_filter"]) if filters["event_id_filter"] else None,
        **filters,
    }


def cached_all_events():
    return cached_metadata("events:all", db_manager.get_all_events)


def cached_projects_by_event(event_id):
    return cached_metadata(f"projects:{event_id}", lambda: db_manager.get_projects_by_event(event_id))


def cached_event(event_id):
    return cached_metadata(f"event:{event_id}", lambda: db_manager.get_event(event_id))


def cached_metadata(key, loader):
    cached = DATA_METADATA_CACHE.get(key)
    now = monotonic()
    if cached and now - cached["time"] < DATA_CACHE_TTL_SECONDS:
        return cached["value"]
    value = loader()
    DATA_METADATA_CACHE[key] = {"time": now, "value": value}
    return value


def build_student_rows(students, projects, include_details=False):
    custom_values = get_custom_values_for_rows(students, include_details)
    project_names = {project[0]: project[1] for project in projects}
    return [build_student_row(student, custom_values, project_names) for student in students]


def get_custom_values_for_rows(students, include_details):
    if not include_details:
        return {}
    return db_manager.get_field_values_by_student_ids([student[0] for student in students])


def build_student_row(student, custom_values, project_names):
    credential_token, qr_path = student_existing_credential(student)
    return {
        "id": student[0],
        "first_name": student[1],
        "last_name_p": student[2],
        "last_name_m": student[3],
        "matricula": student[4],
        "carrera": student[5],
        "project_id": student[6],
        "event_id": student[7] if len(student) > 7 else None,
        "participant_type": student[8] if len(student) > 8 else "alumno",
        "project_name": student_project_name(student, project_names),
        "credential_token": credential_token or "Legacy",
        "is_legacy_qr": not credential_token,
        "qr_path": qr_path,
        "custom_fields": custom_values.get(student[0], []),
    }


def student_existing_credential(student):
    token = student[10] if len(student) > 10 else None
    qr_path = student[11] if len(student) > 11 else None
    if token and not qr_path:
        qr_path = os.path.join("static/qr_codes", f"{token}.png").replace("\\", "/")
    if not token:
        return None, None
    return token, qr_path


def student_project_name(student, project_names):
    if len(student) > 9 and student[9]:
        return student[9]
    return project_names.get(student[6])


@app.route("/download_all_qrs_pdf")
@login_required
@role_required("admin", "staff")
def download_all_qrs_pdf():
    local_qr_manager = QRManager()
    event_id = request.args.get("event_id") or None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ensure_report_dir()
    pdf_path = os.path.join(report_dir, f"qr_codes_{timestamp}.pdf").replace("\\", "/")

    student_data = get_credential_students(local_qr_manager, event_id)
    if not student_data:
        flash("No hay credenciales para descargar", "warning")
        return redirect(url_for("data", event_id=event_id) if event_id else url_for("data"))

    build_standard_credentials_pdf(student_data, pdf_path)
    return send_file(pdf_path, as_attachment=True, download_name="credenciales.pdf")


@app.route("/download_all_credentials_rect_pdf")
@login_required
@role_required("admin", "staff")
def download_all_credentials_rect_pdf():
    local_qr_manager = QRManager()
    event_id = request.args.get("event_id") or None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = ensure_report_dir()
    pdf_path = os.path.join(report_dir, f"credentials_rect_{timestamp}.pdf").replace("\\", "/")

    student_data = get_credential_students(local_qr_manager, event_id)
    if not student_data:
        flash("No hay credenciales para descargar", "warning")
        return redirect(url_for("data", event_id=event_id) if event_id else url_for("data"))

    build_rectangular_credentials_pdf(student_data, pdf_path)
    return send_file(pdf_path, as_attachment=True, download_name="credenciales_rectangulares.pdf")


@app.route("/download_credentials_by_project")
@login_required
@role_required("admin", "staff")
def download_credentials_by_project():
    local_qr_manager = QRManager()
    event_id = request.args.get("event_id") or None
    student_data = get_credential_students(local_qr_manager, event_id)

    if not student_data:
        flash("No hay credenciales para descargar", "warning")
        return redirect(url_for("data", event_id=event_id) if event_id else url_for("data"))

    timestamp = timestamp_slug()
    zip_path = build_project_credentials_zip(student_data, timestamp)
    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"credenciales_por_proyecto_{timestamp}.zip",
    )


def timestamp_slug():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_project_credentials_zip(student_data, timestamp):
    zip_path = os.path.join(ensure_report_dir(), f"credenciales_por_proyecto_{timestamp}.zip").replace("\\", "/")
    grouped_projects = group_students_by_project(student_data)
    write_project_zip(zip_path, grouped_projects)
    return zip_path


def group_students_by_project(student_data):
    grouped = {}
    for student in student_data:
        add_student_to_project_group(grouped, student)
    return grouped


def add_student_to_project_group(grouped, student):
    key = student.get("project_id") or "sin_proyecto"
    grouped.setdefault(key, {"name": student.get("project_name") or "Sin proyecto", "students": []})
    grouped[key]["students"].append(student)


def write_project_zip(zip_path, grouped_projects):
    used_names = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for project_key, project in grouped_projects.items():
            write_project_pdf_to_zip(zf, project_key, project, used_names)


def write_project_pdf_to_zip(zf, project_key, project, used_names):
    pdf_buffer = BytesIO()
    build_rectangular_credentials_pdf(project["students"], pdf_buffer)
    file_name = unique_project_pdf_name(project, project_key, used_names)
    zf.writestr(file_name, pdf_buffer.getvalue())


def unique_project_pdf_name(project, project_key, used_names):
    base_name = safe_filename(project["name"], f"proyecto_{project_key}")
    file_name = f"{base_name}.pdf"
    counter = 2
    while file_name in used_names:
        file_name = f"{base_name}_{counter}.pdf"
        counter += 1
    used_names.add(file_name)
    return file_name


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
    template = db_manager.get_event_template(event_id)
    subject = render_text_template(template["email_subject"], event_name=event[1])
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
        body = render_text_template(
            template["email_body"],
            event_name=event[1],
            participant_list=names,
            recipient=batch["recipient"],
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
        full_name = f"{first_name} {last_name_p} {last_name_m}".strip()
        send_registered_credential_silently(
            event_id,
            project_id,
            participant_type,
            email_value,
            credential,
            qr_path,
            full_name,
            matricula,
        )
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


@app.route("/preview_excel", methods=["POST"])
@login_required
@role_required("admin", api=True)
def preview_excel():
    try:
        file = request.files.get("excel_file") or request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"success": False, "error": "Selecciona un archivo Excel"}), 400
        df, import_format = db_manager.prepare_excel_import_dataframe(file)
        required = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        missing = [column for column in required if column not in df.columns]
        rows = df.head(5).fillna("").to_dict(orient="records")
        return jsonify({
            "success": True,
            "columns": list(df.columns),
            "format": import_format,
            "missing": missing,
            "row_count": len(df.index),
            "preview": rows,
        })
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
        event_type = data.get("event_type") or "entrada"
        result = attendance_manager.register_attendance_by_qr_data(qr_data, event_id, event_type)
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
        report_path = build_requested_report(request.form)
        return jsonify({"success": True, "report_path": report_path})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def build_requested_report(data):
    params = report_request_params(data)
    if params["event_id"]:
        return build_event_report(params)
    return build_legacy_report(params)


def report_request_params(data):
    return {
        "start_date": data.get("start_date") or None,
        "end_date": data.get("end_date") or None,
        "project_id": data.get("project_id") or None,
        "event_id": data.get("event_id") or None,
        "format": data.get("format"),
    }


def build_event_report(params):
    if params["format"] == "pdf":
        return db_manager.export_event_attendance_to_pdf(params["event_id"], params["project_id"], params["start_date"], params["end_date"])
    if params["format"] == "excel":
        return db_manager.export_event_attendance_to_excel(params["event_id"], params["project_id"], params["start_date"], params["end_date"])
    raise ValueError("Formato no soportado")


def build_legacy_report(params):
    report_data = attendance_manager.get_attendance_report(params["start_date"], params["end_date"], params["project_id"])
    if not report_data:
        raise ValueError("No hay datos para el reporte")
    if params["format"] == "pdf":
        return build_legacy_report_pdf(report_data)
    if params["format"] == "excel":
        return build_legacy_report_excel(report_data)
    raise ValueError("Formato no soportado")


def legacy_report_path(extension):
    return os.path.join(ensure_report_dir(), f"report_{timestamp_slug()}.{extension}").replace("\\", "/")


def build_legacy_report_pdf(report_data):
    report_path = legacy_report_path("pdf")
    doc = SimpleDocTemplate(report_path, pagesize=letter)
    doc.build(legacy_report_pdf_elements(report_data))
    return report_path


def legacy_report_pdf_elements(report_data):
    styles = getSampleStyleSheet()
    table = Table(legacy_report_table_data(report_data))
    table.setStyle(legacy_report_table_style())
    return [Paragraph("Reporte de Asistencias - AsisTec", styles["Title"]), table]


def legacy_report_table_data(report_data):
    headers = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Fecha/Hora"]
    return [headers] + [legacy_report_row(row) for row in report_data]


def legacy_report_row(row):
    return [row[0], row[1], row[2], row[3], row[4], row[5] or "Sin proyecto", row[6].strftime("%Y-%m-%d %H:%M:%S")]


def legacy_report_table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ])


def build_legacy_report_excel(report_data):
    report_path = legacy_report_path("xlsx")
    workbook = xlsxwriter.Workbook(report_path)
    write_legacy_report_sheet(workbook, report_data)
    workbook.close()
    return report_path


def write_legacy_report_sheet(workbook, report_data):
    worksheet = workbook.add_worksheet()
    worksheet.write("A1", "Reporte de Asistencias - AsisTec")
    for col, header in enumerate(legacy_report_table_data([])[0]):
        worksheet.write(1, col, header)
    for row_idx, row in enumerate(report_data, 2):
        write_legacy_report_excel_row(worksheet, row_idx, row)


def write_legacy_report_excel_row(worksheet, row_idx, row):
    for col, value in enumerate(legacy_report_row(row)):
        worksheet.write(row_idx, col, value)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=str_to_bool(os.getenv("FLASK_DEBUG"), default=True),
    )
