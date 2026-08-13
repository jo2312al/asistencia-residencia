from io import BytesIO
import os
import unicodedata
import uuid
import zipfile
from datetime import datetime
from functools import wraps
from time import monotonic
from html import escape

import mysql.connector
import pandas as pd
import xlsxwriter
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash

from aplicacion.credenciales_pdf import (
    construir_credencial_tarjeta,
    construir_horizontal_credencial_tarjeta,
    construir_horizontal_credenciales_pdf,
    construir_estandar_credenciales_pdf,
)
from aplicacion.servicio_qr import ServicioCredencialesQR
from attendance_manager import GestorAsistencia
from database import GestorBaseDatos
from qr_manager import GestorQR
from infraestructura.correo import ServicioCorreo

VALID_ROLES = ("adminsuperior", "admin", "staff", "guest")
ROLES_EVENTOS_GLOBALES = {"adminsuperior", "admin"}
ROLES_OPERATIVOS_POR_EVENTO = {"staff", "guest"}
ROLE_LABELS = {
    "adminsuperior": "Administrador superior",
    "admin": "Administrador de evento",
    "staff": "Staff",
    "guest": "Consulta",
}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CACHE_TTL_SECONDS = int(os.getenv("DATA_CACHE_TTL_SECONDS", "30"))
DATA_METADATA_CACHE = {}

load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))


def str_to_bool(value, default=False):
    """Ejecuta la operaciÃ³n str to bool y devuelve el resultado correspondiente."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def obtener_bd_configuracion():
    """Ejecuta la operaciÃ³n obtener bd configuracion y devuelve el resultado correspondiente."""
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


def obtener_rol_inicio_destino(role):
    """Ejecuta la operaciÃ³n obtener rol inicio destino y devuelve el resultado correspondiente."""
    role_map = {
        "adminsuperior": "acceso.panel_sistema",
        "admin": "acceso.admin_panel",
        "staff": "acceso.staff_panel",
        "guest": "acceso.guest_panel",
    }
    return role_map.get(role, "acceso.guest_panel")


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

db_config = obtener_bd_configuracion()
db_manager = GestorBaseDatos(db_config)

BASE_REGISTRATION_FIELD_NAMES = {"nombre", "apellido paterno", "apellido materno", "matricula", "carrera"}


def normalizar_campo_nombre(value):
    """Ejecuta la operaciÃ³n normalizar campo nombre y devuelve el resultado correspondiente."""
    text = unicodedata.normalize("NFD", value or "")
    return "".join(char for char in text if unicodedata.category(char) != "Mn").strip().lower()


def obtener_correo_configuracion():
    """Ejecuta la operaciÃ³n obtener correo configuracion y devuelve el resultado correspondiente."""
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


def enviar_correo(recipient, subject, body, attachments=None):
    """Ejecuta la operaciÃ³n enviar correo y devuelve el resultado correspondiente."""
    return ServicioCorreo(obtener_correo_configuracion()).enviar(recipient, subject, body, attachments)



def enviar_registered_credencial_silenciosamente(event_id, project_id, participant_type, recipient_email, credential, qr_path, full_name, matricula):
    """Ejecuta la operaciÃ³n enviar registered credencial silenciosamente y devuelve el resultado correspondiente."""
    if not event_id:
        return

    try:
        event = db_manager.obtener_evento(event_id)
        if not event:
            return

        recipient = recipient_email
        if project_id and (participant_type or "").lower() != "asesor":
            for row in db_manager.obtener_evento_credencial_filas(event_id):
                if row.get("project_id") == int(project_id) and (row.get("participant_type") or "").lower() == "asesor" and row.get("email"):
                    recipient = row["email"]
                    break

        if not recipient:
            return

        url_digital = asegurar_url_digital_credencial(credential)
        subject = f"Credencial para {event[1]}"
        body = (
            f"Hola,\n\nSe registro la credencial de {full_name} para {event[1]}.\n\n"
            f"Matricula: {matricula}\n"
            f"Credencial digital: {url_digital}\n\n"
            "Adjuntamos el QR para presentarlo en el registro de asistencia.\n"
        )
        filename = f"{matricula or credential['token']}.png"
        enviar_correo(recipient, subject, body, [(qr_path, filename)])
        db_manager.actualizar_credenciales_sent_estado([credential["id"]], "sent")
        db_manager.registrar_bitacora_correo(event_id, recipient, subject, "sent")
    except Exception as exc:
        try:
            db_manager.actualizar_credenciales_sent_estado([credential["id"]], "error")
            db_manager.registrar_bitacora_correo(event_id, recipient_email or "sin destinatario", "Credencial", "error", str(exc))
        except Exception:
            pass


qr_manager = GestorQR()
attendance_manager = GestorAsistencia(db_manager)
servicio_qr = ServicioCredencialesQR(
    db_manager, qr_manager,
    trabajadores=int(os.getenv("QR_BACKGROUND_WORKERS", "2")),
    registrador=app.logger,
)



def url_publica(path):
    """Ejecuta la operaciÃ³n url publica y devuelve el resultado correspondiente."""
    base_url = os.getenv("PUBLIC_BASE_URL", "https://18-223-120-47.sslip.io:8080").rstrip("/")
    return f"{base_url}{path}"


def url_credencial_digital(token_credencial):
    """Ejecuta la operaciÃ³n url credencial digital y devuelve el resultado correspondiente."""
    path = url_for("participantes.credencial_digital", token_credencial=token_credencial)
    return url_publica(path)


def asegurar_url_digital_credencial(credencial):
    """Ejecuta la operaciÃ³n asegurar url digital credencial y devuelve el resultado correspondiente."""
    token_credencial = credencial["token"]
    url_digital = url_credencial_digital(token_credencial)
    if credencial.get("digital_url") != url_digital:
        db_manager.actualizar_url_digital_credencial(token_credencial, url_digital)
    return url_digital


def ruta_publica_qr(ruta_qr):
    """Ejecuta la operaciÃ³n ruta publica qr y devuelve el resultado correspondiente."""
    if not ruta_qr:
        return ""
    ruta_limpia = ruta_qr.replace("\\", "/")
    if ruta_limpia.startswith("static/"):
        return url_for("static", filename=ruta_limpia[len("static/"):])
    return f"/{ruta_limpia.lstrip('/')}"

def asegurar_credencial_qr(credential):
    """Ejecuta la operaciÃ³n asegurar credencial qr y devuelve el resultado correspondiente."""
    return servicio_qr.asegurar_credencial(credential)



def asegurar_participante_qr(student, qr_tool=None):
    """Ejecuta la operaciÃ³n asegurar participante qr y devuelve el resultado correspondiente."""
    return servicio_qr.asegurar_participante(student, qr_tool)



def asegurar_fila_qr(row, qr_tool=None):
    """Ejecuta la operaciÃ³n asegurar fila qr y devuelve el resultado correspondiente."""
    return servicio_qr.asegurar_fila(row, qr_tool)



def participante_dict_a_tuple(student):
    """Ejecuta la operaciÃ³n student dict to tuple y devuelve el resultado correspondiente."""
    return servicio_qr.diccionario_a_tupla(student)



def encolar_qr_generacion(students):
    """Ejecuta la operaciÃ³n encolar qr generacion y devuelve el resultado correspondiente."""
    return servicio_qr.encolar(students)



def fila_needs_qr(student):
    """Ejecuta la operaciÃ³n row needs qr y devuelve el resultado correspondiente."""
    return servicio_qr.necesita_qr(student)



def enviar_qr_generacion(student):
    """Ejecuta la operaciÃ³n enviar qr generacion y devuelve el resultado correspondiente."""
    return servicio_qr.enviar_trabajo(student)



def marcar_qr_trabajo_enviado(key):
    """Ejecuta la operaciÃ³n marcar qr trabajo enviado y devuelve el resultado correspondiente."""
    return servicio_qr.marcar_enviado(key)



def desmarcar_qr_trabajo_enviado(key):
    """Ejecuta la operaciÃ³n desmarcar qr trabajo enviado y devuelve el resultado correspondiente."""
    return servicio_qr.desmarcar_enviado(key)



def generar_participante_qr_segundo_plano(student):
    """Ejecuta la operaciÃ³n generar participante qr segundo plano y devuelve el resultado correspondiente."""
    return servicio_qr._generar_en_segundo_plano(student)







def obtener_credencial_participantes(qr_tool=None, event_id=None):
    """Ejecuta la operaciÃ³n obtener credencial participantes y devuelve el resultado correspondiente."""
    local_qr_manager = qr_tool or GestorQR()
    student_data = []
    for row in db_manager.obtener_participantes_para_credenciales(event_id):
        matricula = row["matricula"]
        qr_path, credential_token, is_legacy_qr = asegurar_fila_qr(row, local_qr_manager)
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


def seguro_nombre_archivo(value, fallback="archivo"):
    """Ejecuta la operaciÃ³n seguro nombre archivo y devuelve el resultado correspondiente."""
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = "".join(char if char.isalnum() else "_" for char in text.lower())
    text = "_".join(part for part in text.split("_") if part)
    return text or fallback


def asegurar_reporte_direccion():
    """Ejecuta la operaciÃ³n asegurar reporte direccion y devuelve el resultado correspondiente."""
    report_dir = "static/reports"
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)
    return report_dir





default_admin_username = os.getenv("ADMIN_USERNAME")
default_admin_password = os.getenv("ADMIN_PASSWORD")
if default_admin_username and default_admin_password:
    default_hash = generate_password_hash(default_admin_password, method="pbkdf2:sha256")
    db_manager.asegurar_usuario(default_admin_username, default_hash, role=os.getenv("ADMIN_ROLE", "adminsuperior"))

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "acceso.iniciar_sesion"


class User(UserMixin):
    def __init__(self, username, role="guest"):
        """Realiza internamente la operaciÃ³n init."""
        self.id = username
        self.role = role

    @property
    def es_admin(self):
        """Ejecuta la operaciÃ³n is admin y devuelve el resultado correspondiente."""
        return self.role in ROLES_EVENTOS_GLOBALES

    @property
    def es_adminsuperior(self):
        """Ejecuta la operaciÃ³n is adminsuperior y devuelve el resultado correspondiente."""
        return self.role == "adminsuperior"

    @property
    def es_staff(self):
        """Ejecuta la operaciÃ³n is staff y devuelve el resultado correspondiente."""
        return self.role == "staff"

    @property
    def es_guest(self):
        """Ejecuta la operaciÃ³n is guest y devuelve el resultado correspondiente."""
        return self.role == "guest"


def rol_requeridos(*roles, api=False):
    """Ejecuta la operaciÃ³n role required y devuelve el resultado correspondiente."""
    def decorator(view_func):
        """Ejecuta la operaciÃ³n decorator y devuelve el resultado correspondiente."""
        @wraps(view_func)
        def envuelto(*args, **kwargs):
            """Ejecuta la operaciÃ³n wrapped y devuelve el resultado correspondiente."""
            if current_user.role not in roles:
                message = "No autorizado para esta accion"
                if api or request.is_json:
                    return jsonify({"success": False, "error": message}), 403
                flash(message, "warning")
                return redirect(url_for("acceso.rol_inicio"))
            return view_func(*args, **kwargs)

        return envuelto

    return decorator


@app.context_processor
def inyectar_plantilla_auxiliares():
    """Ejecuta la operaciÃ³n inyectar plantilla auxiliares y devuelve el resultado correspondiente."""
    return {
        "VALID_ROLES": VALID_ROLES,
        "ROLE_LABELS": ROLE_LABELS,
        "datetime_local_value": datetime_local_value,
    }


def renderizar_texto_plantilla(template, **values):
    """Ejecuta la operaciÃ³n renderizar texto plantilla y devuelve el resultado correspondiente."""
    text = template or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value or ""))
    return text


def datetime_local_value(value):
    """Ejecuta la operaciÃ³n datetime local value y devuelve el resultado correspondiente."""
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%dT%H:%M")
    return str(value).replace(" ", "T")[:16]




def evento_permitido_para_usuario(event_id):
    """Ejecuta la operaciÃ³n evento permitido para usuario y devuelve el resultado correspondiente."""
    event_id_normalizado = normalizar_evento_permiso(event_id)
    if current_user.role in ROLES_EVENTOS_GLOBALES:
        return True
    if not event_id_normalizado:
        return False
    return event_id_normalizado in eventos_permitidos_usuario_actual()


def normalizar_evento_permiso(event_id):
    """Ejecuta la operaciÃ³n normalizar evento permiso y devuelve el resultado correspondiente."""
    try:
        return int(event_id) if event_id else None
    except (TypeError, ValueError):
        return None


def respuesta_evento_no_autorizado(api=False):
    """Ejecuta la operaciÃ³n respuesta evento no autorizado y devuelve el resultado correspondiente."""
    mensaje = "No tienes permiso para consultar este evento"
    if api:
        return jsonify({"success": False, "error": mensaje}), 403
    flash(mensaje, "warning")
    return redirect(url_for("acceso.rol_inicio"))


def evento_no_autorizado(event_id):
    """Ejecuta la operaciÃ³n evento no autorizado y devuelve el resultado correspondiente."""
    return event_id and not evento_permitido_para_usuario(event_id)

def filtrar_eventos_para_actual_usuario(events):
    """Ejecuta la operaciÃ³n filtrar eventos para actual usuario y devuelve el resultado correspondiente."""
    if not current_user.is_authenticated:
        return []
    if current_user.role in ROLES_EVENTOS_GLOBALES:
        return events
    allowed_set = eventos_permitidos_usuario_actual()
    return [event for event in events if event[0] in allowed_set]


def eventos_permitidos_usuario_actual():
    """Ejecuta la operaciÃ³n eventos permitidos usuario actual y devuelve el resultado correspondiente."""
    if not current_user.is_authenticated:
        return set()
    return set(db_manager.obtener_usuario_evento_permisos(current_user.id))


def usuario_operativo_sin_eventos():
    """Ejecuta la operaciÃ³n usuario operativo sin eventos y devuelve el resultado correspondiente."""
    return current_user.role in ROLES_OPERATIVOS_POR_EVENTO and not eventos_permitidos_usuario_actual()


@login_manager.user_loader
def cargar_usuario(username):
    """Ejecuta la operaciÃ³n cargar usuario y devuelve el resultado correspondiente."""
    user_data = db_manager.obtener_usuario(username)
    if user_data:
        return User(user_data[0], user_data[2] or "guest")
    return None


























def normalizar_fecha_hora_entrada(value):
    """Ejecuta la operaciÃ³n normalizar fecha hora entrada y devuelve el resultado correspondiente."""
    if not value:
        return None
    return value.replace("T", " ")










































def evento_id_desde_proyecto(project):
    """Ejecuta la operaciÃ³n event id desde proyecto y devuelve el resultado correspondiente."""
    return project[4] if len(project) > 4 else None


def resumen_evento_seguro(event_id):
    """Ejecuta la operaciÃ³n resumen evento seguro y devuelve el resultado correspondiente."""
    if not event_id:
        return resumen_evento_vacio()
    return db_manager.resumen_ejecutivo_evento(event_id)


def proyectos_asistencia_seguro(event_id):
    """Ejecuta la operaciÃ³n proyectos asistencia seguro y devuelve el resultado correspondiente."""
    return db_manager.proyectos_con_asistencia(event_id) if event_id else []


def resumen_evento_vacio():
    """Ejecuta la operaciÃ³n resumen evento vacio y devuelve el resultado correspondiente."""
    return {"participantes": 0, "asistencias": 0, "presentes": 0, "pendientes": 0, "porcentaje": 0, "alumnos": 0, "asesores": 0, "hora_pico": "Sin registros"}






def obtener_participante_filtros():
    """Ejecuta la operaciÃ³n obtener participante filtros y devuelve el resultado correspondiente."""
    return {
        "matricula_search": request.args.get("matricula", "").strip(),
        "apellido_p_search": request.args.get("apellido_p", "").strip(),
        "project_id_filter": request.args.get("project_id", ""),
        "event_id_filter": request.args.get("event_id", ""),
        "sort_by": valido_orden(request.args.get("sort", "proyecto")),
        "sort_dir": valido_orden_direccion(request.args.get("dir", "asc")),
        "show_details": request.args.get("details") == "1",
        "page": request.args.get("page", 1, type=int),
        "per_page": 10,
    }


def valido_orden(sort_by):
    """Ejecuta la operaciÃ³n valido orden y devuelve el resultado correspondiente."""
    allowed = {"matricula", "nombre", "apellido_p", "apellido_m", "carrera", "tipo", "proyecto"}
    return sort_by if sort_by in allowed else "proyecto"


def valido_orden_direccion(sort_dir):
    """Ejecuta la operaciÃ³n valido orden direccion y devuelve el resultado correspondiente."""
    return sort_dir if sort_dir in {"asc", "desc"} else "asc"


def obtener_participante_pagina(filters):
    """Ejecuta la operaciÃ³n obtener participante pagina y devuelve el resultado correspondiente."""
    page = max(1, filters["page"])
    rows, total = consultar_filtrados_participantes_pagina(filters, page)
    if not rows and page > 1:
        total = contar_filtrados_participantes(filters)
        page = max(1, min(page, max((total + filters["per_page"] - 1) // filters["per_page"], 1)))
        rows, total = consultar_filtrados_participantes_pagina(filters, page)
    total_pages = max((total + filters["per_page"] - 1) // filters["per_page"], 1)
    return {"rows": rows, "page": page, "total_pages": total_pages, "total": total}


def contar_filtrados_participantes(filters):
    """Ejecuta la operaciÃ³n contar filtrados participantes y devuelve el resultado correspondiente."""
    if not filters["event_id_filter"]:
        return 0
    return db_manager.contar_participantes_filtrados(*participante_filtrar_argumentos(filters))


def participante_filtrar_argumentos(filters):
    """Ejecuta la operaciÃ³n participante filtrar argumentos y devuelve el resultado correspondiente."""
    return (
        filters["matricula_search"],
        filters["apellido_p_search"],
        filters["project_id_filter"],
        filters["event_id_filter"],
    )


def consultar_filtrados_participantes(filters, page):
    """Ejecuta la operaciÃ³n consultar filtrados participantes y devuelve el resultado correspondiente."""
    if not filters["event_id_filter"]:
        return []
    offset = (page - 1) * filters["per_page"]
    return db_manager.obtener_todos_participantes_filtrados(
        *participante_filtrar_argumentos(filters),
        filters["sort_by"],
        filters["sort_dir"],
        filters["per_page"],
        offset,
    )


def consultar_filtrados_participantes_pagina(filters, page):
    """Ejecuta la operaciÃ³n consultar filtrados participantes pagina y devuelve el resultado correspondiente."""
    if not filters["event_id_filter"]:
        return [], 0
    offset = (page - 1) * filters["per_page"]
    return db_manager.obtener_participantes_filtrados_pagina(
        *participante_filtrar_argumentos(filters),
        filters["sort_by"],
        filters["sort_dir"],
        filters["per_page"],
        offset,
    )


def construir_datos_contexto(filters, page_data):
    """Ejecuta la operaciÃ³n construir datos contexto y devuelve el resultado correspondiente."""
    projects = cached_proyectos_por_evento(filters["event_id_filter"]) if filters["event_id_filter"] else []
    encolar_qr_generacion(page_data["rows"])
    students = construir_participante_filas(page_data["rows"], projects, filters["show_details"])
    return {
        "students": students,
        "projects": projects,
        "events": filtrar_eventos_para_actual_usuario(cached_all_events()),
        "page": page_data["page"],
        "total_pages": page_data["total_pages"],
        "total_students": page_data["total"],
        "selected_event": cached_evento(filters["event_id_filter"]) if filters["event_id_filter"] else None,
        **filters,
    }


def cached_all_events():
    """Ejecuta la operaciÃ³n cached all events y devuelve el resultado correspondiente."""
    return cached_metadata("events:all", db_manager.obtener_todos_eventos)


def cached_proyectos_por_evento(event_id):
    """Ejecuta la operaciÃ³n cached projects by event y devuelve el resultado correspondiente."""
    return cached_metadata(f"projects:{event_id}", lambda: db_manager.obtener_proyectos_por_evento(event_id))


def cached_evento(event_id):
    """Ejecuta la operaciÃ³n cached event y devuelve el resultado correspondiente."""
    return cached_metadata(f"event:{event_id}", lambda: db_manager.obtener_evento(event_id))


def cached_metadata(key, loader):
    """Ejecuta la operaciÃ³n cached metadata y devuelve el resultado correspondiente."""
    cached = DATA_METADATA_CACHE.get(key)
    now = monotonic()
    if cached and now - cached["time"] < DATA_CACHE_TTL_SECONDS:
        return cached["value"]
    value = loader()
    DATA_METADATA_CACHE[key] = {"time": now, "value": value}
    return value


def construir_participante_filas(students, projects, include_details=False):
    """Ejecuta la operaciÃ³n construir participante filas y devuelve el resultado correspondiente."""
    custom_values = obtener_personalizados_valores_para_filas(students, include_details)
    project_names = {project[0]: project[1] for project in projects}
    return [construir_participante_fila(student, custom_values, project_names) for student in students]


def obtener_personalizados_valores_para_filas(students, include_details):
    """Ejecuta la operaciÃ³n obtener personalizados valores para filas y devuelve el resultado correspondiente."""
    if not include_details:
        return {}
    return db_manager.obtener_campo_valores_por_participante_ids([student[0] for student in students])


def construir_participante_fila(student, custom_values, project_names):
    """Ejecuta la operaciÃ³n construir participante fila y devuelve el resultado correspondiente."""
    credential_token, qr_path = participante_existente_credencial(student)
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
        "project_name": participante_proyecto_nombre(student, project_names),
        "credential_token": credential_token or "Legacy",
        "is_legacy_qr": not credential_token,
        "qr_path": qr_path,
        "custom_fields": custom_values.get(student[0], []),
    }


def participante_existente_credencial(student):
    """Ejecuta la operaciÃ³n student existing credential y devuelve el resultado correspondiente."""
    token = student[10] if len(student) > 10 else None
    qr_path = student[11] if len(student) > 11 else None
    if token and not qr_path:
        qr_path = os.path.join("static/qr_codes", f"{token}.png").replace("\\", "/")
    if not token:
        return None, None
    return token, qr_path


def participante_proyecto_nombre(student, project_names):
    """Ejecuta la operaciÃ³n student project name y devuelve el resultado correspondiente."""
    if len(student) > 9 and student[9]:
        return student[9]
    return project_names.get(student[6])











def datos_credencial_digital(credencial):
    """Ejecuta la operaciÃ³n datos credencial digital y devuelve el resultado correspondiente."""
    nombre = credencial.get("full_name") or nombre_completo_credencial(credencial)
    return {
        "nombre": nombre or "Participante",
        "matricula": credencial.get("matricula") or "Sin matricula",
        "carrera": credencial.get("carrera") or "Sin carrera",
        "evento": credencial.get("event_name") or "Evento general",
        "proyecto": credencial.get("project_name") or "Sin proyecto",
        "tipo": credencial.get("participant_type") or "alumno",
        "ubicacion": credencial.get("location") or "",
        "token": credencial.get("token"),
    }


def nombre_completo_credencial(credencial):
    """Ejecuta la operaciÃ³n nombre completo credencial y devuelve el resultado correspondiente."""
    partes = [credencial.get("first_name"), credencial.get("last_name_p"), credencial.get("last_name_m")]
    return " ".join(parte for parte in partes if parte)


def estado_credencial_digital(credencial):
    """Ejecuta la operaciÃ³n estado credencial digital y devuelve el resultado correspondiente."""
    if credencial.get("credential_status") != "active":
        return {"texto": "Inactiva", "clase": "digital-status-danger"}
    if credencial.get("participant_status") != "active":
        return {"texto": "Participante inactivo", "clase": "digital-status-danger"}
    return {"texto": "Activa", "clase": "digital-status-ok"}

def descargar_participante_credencial(student_id, layout):
    """Ejecuta la operaciÃ³n descargar participante credencial y devuelve el resultado correspondiente."""
    student = db_manager.obtener_participante_por_id(student_id)
    if not student:
        flash("Participante no encontrado", "warning")
        return redirect(url_for("participantes.datos"))
    datos = construir_individual_credencial_participante(student)
    pdf = construir_individual_credencial_pdf(datos, layout)
    return enviar_individual_credencial(pdf, datos, layout)


def construir_individual_credencial_participante(student):
    """Ejecuta la operaciÃ³n construir individual credencial participante y devuelve el resultado correspondiente."""
    qr_path, credential_token, is_legacy_qr = asegurar_participante_qr(participante_dict_a_tuple(student), GestorQR())
    project_id = student.get("project_id")
    return {
        "name": f"{student['first_name']} {student['last_name_p']} {student['last_name_m']}",
        "matricula": student["matricula"],
        "credential_token": credential_token or "Legacy",
        "is_legacy_qr": is_legacy_qr,
        "qr_path": qr_path,
        "project_id": project_id,
        "project_name": student.get("project_name") or "Sin proyecto",
        "project_number": (project_id - 1) if project_id else None,
    }


def construir_individual_credencial_pdf(student, layout):
    """Ejecuta la operaciÃ³n construir individual credencial pdf y devuelve el resultado correspondiente."""
    pdf = BytesIO()
    if layout == "horizontal":
        construir_horizontal_credenciales_pdf([student], pdf)
    else:
        construir_estandar_credenciales_pdf([student], pdf)
    pdf.seek(0)
    return pdf


def enviar_individual_credencial(pdf, student, layout):
    """Ejecuta la operaciÃ³n enviar individual credencial y devuelve el resultado correspondiente."""
    suffix = "horizontal" if layout == "horizontal" else "vertical"
    name = seguro_nombre_archivo(student.get("matricula") or student.get("credential_token"), "credencial")
    return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=f"{name}_{suffix}.pdf")








def marca_tiempo_identificador():
    """Ejecuta la operaciÃ³n timestamp slug y devuelve el resultado correspondiente."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def construir_proyecto_credenciales_zip(student_data, timestamp):
    """Ejecuta la operaciÃ³n construir proyecto credenciales zip y devuelve el resultado correspondiente."""
    zip_path = os.path.join(asegurar_reporte_direccion(), f"credenciales_por_proyecto_{timestamp}.zip").replace("\\", "/")
    grouped_projects = agrupar_participantes_por_proyecto(student_data)
    escribir_proyecto_zip(zip_path, grouped_projects)
    return zip_path


def agrupar_participantes_por_proyecto(student_data):
    """Ejecuta la operaciÃ³n agrupar participantes por proyecto y devuelve el resultado correspondiente."""
    grouped = {}
    for student in student_data:
        agregar_participante_a_proyecto_agrupar(grouped, student)
    return grouped


def agregar_participante_a_proyecto_agrupar(grouped, student):
    """Ejecuta la operaciÃ³n agregar participante a proyecto agrupar y devuelve el resultado correspondiente."""
    key = student.get("project_id") or "sin_proyecto"
    grouped.setdefault(key, {"name": student.get("project_name") or "Sin proyecto", "students": []})
    grouped[key]["students"].append(student)


def escribir_proyecto_zip(zip_path, grouped_projects):
    """Ejecuta la operaciÃ³n escribir proyecto zip y devuelve el resultado correspondiente."""
    used_names = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for project_key, project in grouped_projects.items():
            escribir_proyecto_pdf_a_zip(zf, project_key, project, used_names)


def escribir_proyecto_pdf_a_zip(zf, project_key, project, used_names):
    """Ejecuta la operaciÃ³n escribir proyecto pdf a zip y devuelve el resultado correspondiente."""
    pdf_buffer = BytesIO()
    construir_horizontal_credenciales_pdf(project["students"], pdf_buffer)
    file_name = unico_proyecto_pdf_nombre(project, project_key, used_names)
    zf.writestr(file_name, pdf_buffer.getvalue())


def unico_proyecto_pdf_nombre(project, project_key, used_names):
    """Ejecuta la operaciÃ³n unico proyecto pdf nombre y devuelve el resultado correspondiente."""
    base_name = seguro_nombre_archivo(project["name"], f"proyecto_{project_key}")
    file_name = f"{base_name}.pdf"
    counter = 2
    while file_name in used_names:
        file_name = f"{base_name}_{counter}.pdf"
        counter += 1
    used_names.add(file_name)
    return file_name





def linea_credencial_digital_envio(fila):
    """Ejecuta la operaciÃ³n linea credencial digital envio y devuelve el resultado correspondiente."""
    url_digital = asegurar_url_digital_credencial(fila)
    return f"- {fila['full_name']} | Credencial digital: {url_digital}"

def construir_credencial_correo_lotes(event_id):
    """Ejecuta la operaciÃ³n construir credencial correo lotes y devuelve el resultado correspondiente."""
    rows = db_manager.obtener_evento_credencial_filas(event_id)
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












def validar_peticion_asistencia(datos):
    """Ejecuta la operaciÃ³n validar peticion asistencia y devuelve el resultado correspondiente."""
    if not datos or "qr_data" not in datos:
        return "Datos de QR no proporcionados"
    if usuario_operativo_sin_eventos():
        return "No tienes eventos asignados para registrar asistencia"
    event_id = normalizar_evento_asistencia(datos.get("event_id"))
    if evento_no_autorizado(event_id):
        return "No tienes permiso para registrar asistencia en este evento"
    return None


def registrar_asistencia_desde_payload(datos):
    """Ejecuta la operaciÃ³n registrar asistencia desde payload y devuelve el resultado correspondiente."""
    event_id = normalizar_evento_asistencia(datos.get("event_id"))
    event_type = datos.get("event_type") or "entrada"
    return attendance_manager.registrar_por_datos_qr(datos["qr_data"], event_id, event_type)


def normalizar_evento_asistencia(event_id):
    """Ejecuta la operaciÃ³n normalizar evento asistencia y devuelve el resultado correspondiente."""
    try:
        return int(event_id) if event_id else None
    except (TypeError, ValueError):
        return None


def respuesta_registro_asistencia(result):
    """Ejecuta la operaciÃ³n respuesta registro asistencia y devuelve el resultado correspondiente."""
    if result == "Asistencia registrada exitosamente":
        return jsonify({"success": True, "message": result})
    if resultado_es_duplicado(result):
        return jsonify({"success": False, "duplicate": True, "error": result})
    return jsonify({"success": False, "error": result}), 400


def resultado_es_duplicado(resultado):
    """Ejecuta la operaciÃ³n resultado es duplicado y devuelve el resultado correspondiente."""
    texto = (resultado or "").lower()
    return "duplicada" in texto or "ya fue tomado" in texto










def construir_solicitado_reporte(datos):
    """Ejecuta la operaciÃ³n construir solicitado reporte y devuelve el resultado correspondiente."""
    params = reporte_request_params(datos)
    if evento_no_autorizado(params["event_id"]):
        raise PermissionError("No tienes permiso para generar reportes de este evento")
    if params["report_type"] == "final" and not params["event_id"]:
        raise ValueError("Selecciona un evento para generar el reporte final oficial")
    if params["event_id"]:
        return construir_evento_reporte(params)
    return construir_heredado_reporte(params)


def reporte_request_params(datos):
    """Ejecuta la operaciÃ³n report request params y devuelve el resultado correspondiente."""
    return {
        "start_date": datos.get("start_date") or None,
        "end_date": datos.get("end_date") or None,
        "project_id": datos.get("project_id") or None,
        "event_id": datos.get("event_id") or None,
        "format": datos.get("format"),
        "report_type": datos.get("report_type") or "asistencia",
    }


def construir_evento_reporte(params):
    """Ejecuta la operaciÃ³n construir evento reporte y devuelve el resultado correspondiente."""
    if params["report_type"] == "final" and params["format"] == "pdf":
        return db_manager.exportar_reporte_final_evento_pdf(params["event_id"])
    if params["report_type"] == "final" and params["format"] == "excel":
        return db_manager.exportar_reporte_final_evento_excel(params["event_id"])
    if params["format"] == "pdf":
        return db_manager.exportar_evento_asistencia_a_pdf(params["event_id"], params["project_id"], params["start_date"], params["end_date"])
    if params["format"] == "excel":
        return db_manager.exportar_evento_asistencia_a_excel(params["event_id"], params["project_id"], params["start_date"], params["end_date"])
    raise ValueError("Formato no soportado")


def construir_heredado_reporte(params):
    """Ejecuta la operaciÃ³n construir heredado reporte y devuelve el resultado correspondiente."""
    report_data = attendance_manager.obtener_reporte(params["start_date"], params["end_date"], params["project_id"])
    if not report_data:
        raise ValueError("No hay datos para el reporte")
    if params["format"] == "pdf":
        return construir_heredado_reporte_pdf(report_data)
    if params["format"] == "excel":
        return construir_heredado_reporte_excel(report_data)
    raise ValueError("Formato no soportado")


def heredado_reporte_ruta(extension):
    """Ejecuta la operaciÃ³n legacy report path y devuelve el resultado correspondiente."""
    return os.path.join(asegurar_reporte_direccion(), f"report_{marca_tiempo_identificador()}.{extension}").replace("\\", "/")


def construir_heredado_reporte_pdf(report_data):
    """Ejecuta la operaciÃ³n construir heredado reporte pdf y devuelve el resultado correspondiente."""
    report_path = heredado_reporte_ruta("pdf")
    doc = heredado_reporte_pdf_doc(report_path)
    doc.build(heredado_reporte_pdf_elements(report_data))
    return report_path


def heredado_reporte_pdf_doc(report_path):
    """Ejecuta la operaciÃ³n legacy report pdf doc y devuelve el resultado correspondiente."""
    return SimpleDocTemplate(
        report_path, pagesize=landscape(letter),
        leftMargin=0.35 * inch, rightMargin=0.35 * inch,
        topMargin=0.35 * inch, bottomMargin=0.35 * inch,
    )


def heredado_reporte_pdf_elements(report_data):
    """Ejecuta la operaciÃ³n legacy report pdf elements y devuelve el resultado correspondiente."""
    styles = heredado_reporte_pdf_styles()
    table = heredado_reporte_pdf_tabla(report_data, styles)
    return heredado_reporte_pdf_encabezado(len(report_data), styles) + [table]


def heredado_reporte_pdf_styles():
    """Ejecuta la operaciÃ³n legacy report pdf styles y devuelve el resultado correspondiente."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("ReportMeta", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#475569")))
    styles.add(ParagraphStyle("ReportCell", parent=styles["Normal"], fontSize=6.8, leading=8, textColor=colors.HexColor("#0f172a")))
    styles.add(ParagraphStyle("ReportHeader", parent=styles["Normal"], fontSize=7, leading=8, textColor=colors.white, alignment=1))
    return styles


def heredado_reporte_pdf_encabezado(total, styles):
    """Ejecuta la operaciÃ³n legacy report pdf header y devuelve el resultado correspondiente."""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return [
        Paragraph("Reporte de asistencias - AsisTec", styles["Title"]),
        Paragraph(f"Total de registros: {total} | Generado: {generated}", styles["ReportMeta"]),
        Spacer(1, 0.12 * inch),
    ]


def heredado_reporte_pdf_tabla(report_data, styles):
    """Ejecuta la operaciÃ³n legacy report pdf table y devuelve el resultado correspondiente."""
    rows = heredado_reporte_tabla_datos(report_data)
    table_data = [[heredado_pdf_celda(value, styles["ReportHeader" if idx == 0 else "ReportCell"]) for value in row] for idx, row in enumerate(rows)]
    table = Table(table_data, colWidths=heredado_reporte_pdf_widths(), repeatRows=1)
    table.setStyle(heredado_reporte_tabla_style())
    return table


def heredado_pdf_celda(value, style):
    """Ejecuta la operaciÃ³n legacy pdf cell y devuelve el resultado correspondiente."""
    return Paragraph(escape(str(value or "")), style)


def heredado_reporte_pdf_widths():
    """Ejecuta la operaciÃ³n legacy report pdf widths y devuelve el resultado correspondiente."""
    return [value * inch for value in [0.9, 1.05, 1.0, 1.0, 1.55, 1.45, 1.15]]


def heredado_reporte_tabla_datos(report_data):
    """Ejecuta la operaciÃ³n legacy report table data y devuelve el resultado correspondiente."""
    headers = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Fecha/Hora"]
    return [headers] + [heredado_reporte_fila(row) for row in report_data]


def heredado_reporte_fila(row):
    """Ejecuta la operaciÃ³n legacy report row y devuelve el resultado correspondiente."""
    return [row[0], row[1], row[2], row[3], row[4], row[5] or "Sin proyecto", row[6].strftime("%Y-%m-%d %H:%M:%S")]


def heredado_reporte_tabla_style():
    """Ejecuta la operaciÃ³n legacy report table style y devuelve el resultado correspondiente."""
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#08223c")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dbe4ee")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])


def construir_heredado_reporte_excel(report_data):
    """Ejecuta la operaciÃ³n construir heredado reporte excel y devuelve el resultado correspondiente."""
    report_path = heredado_reporte_ruta("xlsx")
    workbook = xlsxwriter.Workbook(report_path)
    escribir_heredado_reporte_hoja(workbook, report_data)
    workbook.close()
    return report_path


def escribir_heredado_reporte_hoja(workbook, report_data):
    """Ejecuta la operaciÃ³n escribir heredado reporte hoja y devuelve el resultado correspondiente."""
    worksheet = workbook.add_worksheet()
    worksheet.write("A1", "Reporte de Asistencias - AsisTec")
    for col, header in enumerate(heredado_reporte_tabla_datos([])[0]):
        worksheet.write(1, col, header)
    for row_idx, row in enumerate(report_data, 2):
        escribir_heredado_reporte_excel_fila(worksheet, row_idx, row)


def escribir_heredado_reporte_excel_fila(worksheet, row_idx, row):
    """Ejecuta la operaciÃ³n escribir heredado reporte excel fila y devuelve el resultado correspondiente."""
    for col, value in enumerate(heredado_reporte_fila(row)):
        worksheet.write(row_idx, col, value)



# Blueprints de presentaciÃ³n: se registran despuÃ©s de construir servicios y helpers.
from presentacion.rutas.acceso import crear_blueprint as crear_blueprint_acceso
from presentacion.rutas.usuarios import crear_blueprint as crear_blueprint_usuarios
from presentacion.rutas.eventos import crear_blueprint as crear_blueprint_eventos
from presentacion.rutas.participantes import crear_blueprint as crear_blueprint_participantes
from presentacion.rutas.configuracion import crear_blueprint as crear_blueprint_configuracion
from presentacion.rutas.asistencia import crear_blueprint as crear_blueprint_asistencia
from presentacion.rutas.reportes import crear_blueprint as crear_blueprint_reportes

_contexto_rutas = globals().copy()
app.register_blueprint(crear_blueprint_acceso(_contexto_rutas))
app.register_blueprint(crear_blueprint_usuarios(_contexto_rutas))
app.register_blueprint(crear_blueprint_eventos(_contexto_rutas))
app.register_blueprint(crear_blueprint_participantes(_contexto_rutas))
app.register_blueprint(crear_blueprint_configuracion(_contexto_rutas))
app.register_blueprint(crear_blueprint_asistencia(_contexto_rutas))
app.register_blueprint(crear_blueprint_reportes(_contexto_rutas))

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=str_to_bool(os.getenv("FLASK_DEBUG"), default=True),
    )

# Alias temporales para compatibilidad con la API anterior.
add_student_to_project_group = agregar_participante_a_proyecto_agrupar
build_credential_email_batches = construir_credencial_correo_lotes
build_data_context = construir_datos_contexto
build_event_report = construir_evento_reporte
build_legacy_report = construir_heredado_reporte
build_legacy_report_excel = construir_heredado_reporte_excel
build_legacy_report_pdf = construir_heredado_reporte_pdf
build_project_credentials_zip = construir_proyecto_credenciales_zip
build_requested_report = construir_solicitado_reporte
build_single_credential_pdf = construir_individual_credencial_pdf
build_single_credential_student = construir_individual_credencial_participante
build_student_row = construir_participante_fila
build_student_rows = construir_participante_filas
count_filtered_students = contar_filtrados_participantes
download_student_credential = descargar_participante_credencial
ensure_credential_qr = asegurar_credencial_qr
ensure_report_dir = asegurar_reporte_direccion
ensure_row_qr = asegurar_fila_qr
ensure_student_qr = asegurar_participante_qr
fetch_filtered_students = consultar_filtrados_participantes
fetch_filtered_students_page = consultar_filtrados_participantes_pagina
filter_events_for_current_user = filtrar_eventos_para_actual_usuario
generate_student_qr_background = generar_participante_qr_segundo_plano
get_credential_students = obtener_credencial_participantes
get_custom_values_for_rows = obtener_personalizados_valores_para_filas
get_db_config = obtener_bd_configuracion
get_mail_config = obtener_correo_configuracion
get_participant_filters = obtener_participante_filtros
get_participant_page = obtener_participante_pagina
get_role_home_endpoint = obtener_rol_inicio_destino
group_students_by_project = agrupar_participantes_por_proyecto
inject_template_helpers = inyectar_plantilla_auxiliares
load_user = cargar_usuario
mark_qr_job_submitted = marcar_qr_trabajo_enviado
normalize_datetime_input = normalizar_fecha_hora_entrada
normalize_field_name = normalizar_campo_nombre
queue_qr_generation = encolar_qr_generacion
render_text_template = renderizar_texto_plantilla
safe_filename = seguro_nombre_archivo
send_mail = enviar_correo
send_registered_credential_silently = enviar_registered_credencial_silenciosamente
send_single_credential = enviar_individual_credencial
student_filter_args = participante_filtrar_argumentos
submit_qr_generation = enviar_qr_generacion
unique_project_pdf_name = unico_proyecto_pdf_nombre
unmark_qr_job_submitted = desmarcar_qr_trabajo_enviado
valid_sort = valido_orden
valid_sort_dir = valido_orden_direccion
write_legacy_report_excel_row = escribir_heredado_reporte_excel_fila
write_legacy_report_sheet = escribir_heredado_reporte_hoja
write_project_pdf_to_zip = escribir_proyecto_pdf_a_zip
write_project_zip = escribir_proyecto_zip

# Alias temporales para compatibilidad con la API anterior.
User.is_admin = User.es_admin
User.is_adminsuperior = User.es_adminsuperior
User.is_guest = User.es_guest
User.is_staff = User.es_staff
cached_event = cached_evento
cached_projects_by_event = cached_proyectos_por_evento
event_id_desde_proyecto = evento_id_desde_proyecto
legacy_pdf_cell = heredado_pdf_celda
legacy_report_path = heredado_reporte_ruta
legacy_report_pdf_doc = heredado_reporte_pdf_doc
legacy_report_pdf_elements = heredado_reporte_pdf_elements
legacy_report_pdf_header = heredado_reporte_pdf_encabezado
legacy_report_pdf_styles = heredado_reporte_pdf_styles
legacy_report_pdf_table = heredado_reporte_pdf_tabla
legacy_report_pdf_widths = heredado_reporte_pdf_widths
legacy_report_row = heredado_reporte_fila
legacy_report_table_data = heredado_reporte_tabla_datos
legacy_report_table_style = heredado_reporte_tabla_style
report_request_params = reporte_request_params
role_required = rol_requeridos
row_needs_qr = fila_needs_qr
student_dict_to_tuple = participante_dict_a_tuple
student_existing_credential = participante_existente_credencial
student_project_name = participante_proyecto_nombre
timestamp_slug = marca_tiempo_identificador
