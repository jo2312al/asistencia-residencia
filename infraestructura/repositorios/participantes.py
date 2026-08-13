import hashlib
import os
import re
import secrets
import unicodedata
import uuid
from datetime import datetime
from html import escape

import pandas as pd
import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import generate_password_hash

class RepositorioParticipantesMixin:
    """Operaciones de participantes extraídas de la antigua clase monolítica."""

    def agregar_participante(self, student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id, event_id=None, email=None, participant_type='alumno'):
        """Ejecuta la operación agregar participante y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                  (student_id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id))
        try:
            participant_id = self._asegurar_participante_para_participante(
                c, student_id, first_name, last_name_p, last_name_m, project_id, event_id, email, participant_type
            )
            self._asegurar_credencial_para_participante(c, participant_id)
        except Exception:
            pass
        conn.commit()
        conn.close()

    def _nuevo_credencial_token(self):
        """Realiza internamente la operación new credential token."""
        return f"CRD-{secrets.token_hex(4)}"

    def _asegurar_participante_para_participante(self, cursor, student_id, first_name, last_name_p, last_name_m, project_id, event_id=None, email=None, participant_type='alumno'):
        """Realiza internamente la operación asegurar participante para participante."""
        cursor.execute("SELECT id FROM participants WHERE legacy_student_id = %s", (student_id,))
        existing = cursor.fetchone()
        full_name = f"{first_name} {last_name_p} {last_name_m}".strip()
        now = datetime.now()
        if existing:
            participant_id = existing[0]
            cursor.execute(
                """UPDATE participants
                   SET full_name = %s,
                       email = COALESCE(%s, email),
                       participant_type = COALESCE(%s, participant_type),
                       event_id = %s,
                       project_id = %s,
                       updated_at = %s
                   WHERE id = %s""",
                (full_name, email or None, participant_type or None, event_id, project_id, now, participant_id)
            )
            return participant_id

        participant_id = str(uuid.uuid4())
        cursor.execute(
            """INSERT INTO participants
               (id, full_name, email, participant_type, event_id, project_id, status, legacy_student_id, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)""",
            (participant_id, full_name, email, participant_type or 'alumno', event_id, project_id, student_id, now, now)
        )
        return participant_id

    def _asegurar_credencial_para_participante(self, cursor, participant_id):
        """Realiza internamente la operación asegurar credencial para participante."""
        cursor.execute(
            "SELECT id, token, qr_path, digital_url FROM credentials WHERE participant_id = %s ORDER BY created_at DESC LIMIT 1",
            (participant_id,)
        )
        existing = cursor.fetchone()
        if existing:
            return {"id": existing[0], "token": existing[1], "qr_path": existing[2], "digital_url": existing[3]}

        now = datetime.now()
        for _ in range(5):
            token = self._nuevo_credencial_token()
            try:
                credential_id_type = self._obtener_columna_datos_tipo(cursor, 'credentials', 'id')
                if credential_id_type in ('int', 'bigint', 'mediumint', 'smallint', 'tinyint'):
                    cursor.execute(
                        """INSERT INTO credentials
                           (participant_id, token, status, sent_status, created_at, updated_at)
                           VALUES (%s, %s, 'active', 'pending', %s, %s)""",
                        (participant_id, token, now, now)
                    )
                    credential_id = cursor.lastrowid
                else:
                    credential_id = str(uuid.uuid4())
                    cursor.execute(
                        """INSERT INTO credentials
                           (id, participant_id, token, status, sent_status, created_at, updated_at)
                           VALUES (%s, %s, %s, 'active', 'pending', %s, %s)""",
                        (credential_id, participant_id, token, now, now)
                    )
                return {"id": credential_id, "token": token, "qr_path": None, "digital_url": None}
            except mysql.connector.Error as e:
                if e.errno != 1062:
                    raise
        raise ValueError("No se pudo generar un token unico para la credencial")

    def asegurar_participante_participante_credencial(self, student):
        """Ejecuta la operación asegurar participante participante credencial y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        participant_id = self._asegurar_participante_para_participante(
            c, student[0], student[1], student[2], student[3], student[6], student[7] if len(student) > 7 else None
        )
        credential = self._asegurar_credencial_para_participante(c, participant_id)
        conn.commit()
        conn.close()
        return credential

    def obtener_participante_id_por_participante_id(self, student_id):
        """Ejecuta la operación obtener participante id por participante id y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT id FROM participants WHERE legacy_student_id = %s", (student_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None

    def guardar_participante_campo_valores(self, participant_id, field_values):
        """Ejecuta la operación guardar participante campo valores y devuelve el resultado correspondiente."""
        if not participant_id or not field_values:
            return

        conn = self.conectar()
        c = conn.cursor()
        self._guardar_participante_campo_valores(c, participant_id, field_values)
        conn.commit()
        conn.close()

    def _guardar_participante_campo_valores(self, cursor, participant_id, field_values):
        """Realiza internamente la operación guardar participante campo valores."""
        now = datetime.now()
        for field_id, value in field_values.items():
            cursor.execute(
                """SELECT id FROM participant_field_values
                   WHERE participant_id = %s AND field_id = %s""",
                (participant_id, field_id)
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """UPDATE participant_field_values
                       SET value = %s, updated_at = %s
                       WHERE id = %s""",
                    (value, now, existing[0])
                )
            else:
                cursor.execute(
                    """INSERT INTO participant_field_values
                       (participant_id, field_id, value, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (participant_id, field_id, value, now, now)
                )

    def obtener_campo_valores_por_participante_ids(self, student_ids):
        """Ejecuta la operación obtener campo valores por participante ids y devuelve el resultado correspondiente."""
        if not student_ids:
            return {}

        placeholders = ",".join(["%s"] * len(student_ids))
        conn = self.conectar()
        try:
            c = conn.cursor()
            c.execute(self._campo_valores_query(placeholders), tuple(student_ids) * 2)
            return self._agrupar_campo_valores(c.fetchall())
        finally:
            conn.close()

    def _campo_valores_query(self, placeholders):
        """Realiza internamente la operación field values query."""
        return f"""
            SELECT p.legacy_student_id, pf.name, pfv.value, pf.display_order, pf.id
            FROM participant_field_values pfv
            JOIN project_fields pf ON pfv.field_id = pf.id
            JOIN participants p ON pfv.participant_id = p.id
            WHERE p.legacy_student_id IN ({placeholders})
            UNION ALL
            SELECT p.legacy_student_id, ef.name, pefv.value, ef.display_order, ef.id
            FROM participant_event_field_values pefv
            JOIN event_fields ef ON pefv.field_id = ef.id
            JOIN participants p ON pefv.participant_id = p.id
            WHERE p.legacy_student_id IN ({placeholders})
            ORDER BY 4, 5
        """

    def _agrupar_campo_valores(self, rows):
        """Realiza internamente la operación agrupar campo valores."""
        values = {}
        for student_id, name, value, _, _ in rows:
            values.setdefault(student_id, []).append({"name": name, "value": value})
        return values

    def actualizar_credencial_qr_ruta(self, token, qr_path):
        """Ejecuta la operación actualizar credencial qr ruta y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            "UPDATE credentials SET qr_path = %s, updated_at = %s WHERE token = %s",
            (qr_path, datetime.now(), token)
        )
        conn.commit()
        conn.close()

    def actualizar_url_digital_credencial(self, token, url_digital):
        """Ejecuta la operación actualizar url digital credencial y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            "UPDATE credentials SET digital_url = %s, updated_at = %s WHERE token = %s",
            (url_digital, datetime.now(), token)
        )
        conn.commit()
        conn.close()

    def actualizar_credenciales_sent_estado(self, credential_ids, status):
        """Ejecuta la operación actualizar credenciales sent estado y devuelve el resultado correspondiente."""
        if not credential_ids:
            return
        placeholders = ",".join(["%s"] * len(credential_ids))
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            f"UPDATE credentials SET sent_status = %s, updated_at = %s WHERE id IN ({placeholders})",
            tuple([status, datetime.now()] + credential_ids)
        )
        conn.commit()
        conn.close()

    def registrar_bitacora_correo(self, event_id, recipient, subject, status, error=None):
        """Ejecuta la operación log email y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """INSERT INTO email_logs (event_id, recipient, subject, status, error, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (event_id, recipient, subject, status, error, datetime.now())
        )
        conn.commit()
        conn.close()

    def obtener_evento_credencial_filas(self, event_id):
        """Ejecuta la operación obtener evento credencial filas y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT p.id AS participant_id, p.full_name, p.email, p.participant_type,
                      p.project_id, pr.name AS project_name, e.name AS event_name,
                      s.matricula, c.id AS credential_id, c.token, c.qr_path, c.digital_url
               FROM participants p
               JOIN credentials c ON c.participant_id = p.id
               LEFT JOIN students s ON p.legacy_student_id = s.id
               LEFT JOIN projects pr ON p.project_id = pr.id
               LEFT JOIN events e ON p.event_id = e.id
               WHERE p.event_id = %s AND p.status = 'active' AND c.status = 'active'
               ORDER BY p.project_id, p.participant_type, p.full_name""",
            (event_id,)
        )
        rows = c.fetchall()
        conn.close()
        return rows

    def obtener_evento_correo_registros(self, event_id, limit=25):
        """Ejecuta la operación obtener evento correo registros y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, recipient, subject, status, error, created_at
               FROM email_logs
               WHERE event_id = %s
               ORDER BY created_at DESC
               LIMIT %s""",
            (event_id, int(limit))
        )
        logs = c.fetchall()
        conn.close()
        return logs

    def obtener_evento_asistencia_eventos(self, event_id, limit=25):
        """Ejecuta la operación obtener evento asistencia eventos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT ae.id, ae.event_type, ae.timestamp, p.full_name, s.matricula, pr.name
               FROM attendance_events ae
               LEFT JOIN participants p ON ae.participant_id = p.id
               LEFT JOIN students s ON p.legacy_student_id = s.id
               LEFT JOIN projects pr ON p.project_id = pr.id
               WHERE ae.event_id = %s
               ORDER BY ae.timestamp DESC
               LIMIT %s""",
            (event_id, int(limit))
        )
        rows = c.fetchall()
        conn.close()
        return rows

    def obtener_participantes_para_credenciales(self, event_id=None):
        """Ejecuta la operación obtener participantes para credenciales y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        query = """
            SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera,
                   s.project_id, s.event_id, COALESCE(p.participant_type, 'alumno') AS participant_type,
                   pr.name AS project_name, cr.token AS credential_token, cr.qr_path
            FROM students s
            LEFT JOIN participants p ON p.legacy_student_id = s.id
            LEFT JOIN credentials cr ON cr.participant_id = p.id
            LEFT JOIN projects pr ON s.project_id = pr.id
            WHERE 1=1
        """
        params = []
        if event_id:
            query += " AND s.event_id = %s"
            params.append(event_id)
        query += " ORDER BY pr.name ASC, s.last_name_p ASC, s.first_name ASC, s.matricula ASC"
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        return rows

    def obtener_evento_conteos(self, event_id):
        """Ejecuta la operación obtener evento conteos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM students WHERE event_id = %s", (event_id,))
        participants = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM projects WHERE event_id = %s", (event_id,))
        projects = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM attendance_events WHERE event_id = %s", (event_id,))
        attendance = c.fetchone()[0]
        c.execute(
            """SELECT COUNT(*)
               FROM credentials c
               JOIN participants p ON c.participant_id = p.id
               WHERE p.event_id = %s""",
            (event_id,)
        )
        credentials = c.fetchone()[0]
        conn.close()
        return {
            "participants": participants,
            "projects": projects,
            "attendance": attendance,
            "credentials": credentials,
        }

    def obtener_evento_plantilla(self, event_id):
        """Ejecuta la operación obtener evento plantilla y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT event_id, email_subject, email_body, credential_style, logo_filename, updated_at
               FROM event_templates
               WHERE event_id = %s""",
            (event_id,)
        )
        template = c.fetchone()
        conn.close()
        if template:
            return template
        return {
            "event_id": event_id,
            "email_subject": "Credenciales para {event_name}",
            "email_body": (
                "Hola,\n\nAdjuntamos las credenciales para {event_name}.\n\n"
                "Participantes:\n{participant_list}\n\n"
                "Presenten el QR al momento del registro de asistencia.\n"
            ),
            "credential_style": "standard",
            "logo_filename": "asistec.webp",
            "updated_at": None,
        }

    def guardar_evento_plantilla(self, event_id, email_subject, email_body, credential_style='standard', logo_filename=None):
        """Ejecuta la operación guardar evento plantilla y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        now = datetime.now()
        c.execute(
            """INSERT INTO event_templates
               (event_id, email_subject, email_body, credential_style, logo_filename, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   email_subject = VALUES(email_subject),
                   email_body = VALUES(email_body),
                   credential_style = VALUES(credential_style),
                   logo_filename = VALUES(logo_filename),
                   updated_at = VALUES(updated_at)""",
            (event_id, email_subject, email_body, credential_style or 'standard', logo_filename or None, now)
        )
        conn.commit()
        conn.close()

    def obtener_usuario_evento_permisos(self, username):
        """Ejecuta la operación obtener usuario evento permisos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT event_id
               FROM user_event_permissions
               WHERE username = %s""",
            (username,)
        )
        event_ids = [row[0] for row in c.fetchall()]
        conn.close()
        return event_ids

    def establecer_usuario_evento_permisos(self, username, event_ids):
        """Ejecuta la operación establecer usuario evento permisos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("DELETE FROM user_event_permissions WHERE username = %s", (username,))
        now = datetime.now()
        for event_id in event_ids:
            c.execute(
                """INSERT INTO user_event_permissions (username, event_id, created_at)
                   VALUES (%s, %s, %s)""",
                (username, event_id, now)
            )
        conn.commit()
        conn.close()

    def obtener_credencial_por_token(self, token):
        """Ejecuta la operación obtener credencial por token y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT c.id AS credential_id, c.token, c.status AS credential_status,
                      p.id AS participant_id, p.full_name, p.project_id, p.status AS participant_status,
                      p.legacy_student_id
               FROM credentials c
               JOIN participants p ON c.participant_id = p.id
               WHERE c.token = %s""",
            (token,)
        )
        credential = c.fetchone()
        conn.close()
        return credential

    def obtener_credencial_digital_por_token(self, token):
        """Ejecuta la operación obtener credencial digital por token y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT c.id AS credential_id, c.token, c.status AS credential_status,
                      c.qr_path, c.digital_url, c.created_at, c.updated_at,
                      p.id AS participant_id, p.full_name, p.participant_type,
                      p.project_id, p.status AS participant_status, p.event_id,
                      s.id AS student_id, s.first_name, s.last_name_p, s.last_name_m,
                      s.matricula, s.carrera, pr.name AS project_name, e.name AS event_name,
                      e.start_datetime, e.end_datetime, e.location
               FROM credentials c
               JOIN participants p ON c.participant_id = p.id
               LEFT JOIN students s ON p.legacy_student_id = s.id
               LEFT JOIN projects pr ON p.project_id = pr.id
               LEFT JOIN events e ON p.event_id = e.id
               WHERE c.token = %s""",
            (token,)
        )
        credencial = c.fetchone()
        conn.close()
        return credencial

    def registrar_asistencia_evento(self, participant_id, credential_id, legacy_attendance_id, event_type, timestamp, event_id=None):
        """Ejecuta la operación registrar asistencia evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """INSERT INTO attendance_events
               (participant_id, credential_id, legacy_attendance_id, event_type, timestamp, event_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (participant_id, credential_id, legacy_attendance_id, event_type or 'entrada', timestamp, event_id or None)
        )
        conn.commit()
        conn.close()

# Alias temporales para compatibilidad con la API anterior.
RepositorioParticipantesMixin._ensure_credential_for_participant = RepositorioParticipantesMixin._asegurar_credencial_para_participante
RepositorioParticipantesMixin._ensure_participant_for_student = RepositorioParticipantesMixin._asegurar_participante_para_participante
RepositorioParticipantesMixin._group_field_values = RepositorioParticipantesMixin._agrupar_campo_valores
RepositorioParticipantesMixin._save_participant_field_values = RepositorioParticipantesMixin._guardar_participante_campo_valores
RepositorioParticipantesMixin.add_student = RepositorioParticipantesMixin.agregar_participante
RepositorioParticipantesMixin.ensure_student_participant_credential = RepositorioParticipantesMixin.asegurar_participante_participante_credencial
RepositorioParticipantesMixin.get_credential_by_token = RepositorioParticipantesMixin.obtener_credencial_por_token
RepositorioParticipantesMixin.get_event_attendance_events = RepositorioParticipantesMixin.obtener_evento_asistencia_eventos
RepositorioParticipantesMixin.get_event_counts = RepositorioParticipantesMixin.obtener_evento_conteos
RepositorioParticipantesMixin.get_event_credential_rows = RepositorioParticipantesMixin.obtener_evento_credencial_filas
RepositorioParticipantesMixin.get_event_email_logs = RepositorioParticipantesMixin.obtener_evento_correo_registros
RepositorioParticipantesMixin.get_event_template = RepositorioParticipantesMixin.obtener_evento_plantilla
RepositorioParticipantesMixin.get_field_values_by_student_ids = RepositorioParticipantesMixin.obtener_campo_valores_por_participante_ids
RepositorioParticipantesMixin.get_participant_id_by_student_id = RepositorioParticipantesMixin.obtener_participante_id_por_participante_id
RepositorioParticipantesMixin.get_students_for_credentials = RepositorioParticipantesMixin.obtener_participantes_para_credenciales
RepositorioParticipantesMixin.get_user_event_permissions = RepositorioParticipantesMixin.obtener_usuario_evento_permisos
RepositorioParticipantesMixin.record_attendance_event = RepositorioParticipantesMixin.registrar_asistencia_evento
RepositorioParticipantesMixin.save_event_template = RepositorioParticipantesMixin.guardar_evento_plantilla
RepositorioParticipantesMixin.save_participant_field_values = RepositorioParticipantesMixin.guardar_participante_campo_valores
RepositorioParticipantesMixin.set_user_event_permissions = RepositorioParticipantesMixin.establecer_usuario_evento_permisos
RepositorioParticipantesMixin.update_credential_qr_path = RepositorioParticipantesMixin.actualizar_credencial_qr_ruta
RepositorioParticipantesMixin.update_credentials_sent_status = RepositorioParticipantesMixin.actualizar_credenciales_sent_estado

# Alias temporales para compatibilidad con la API anterior.
RepositorioParticipantesMixin._field_values_query = RepositorioParticipantesMixin._campo_valores_query
RepositorioParticipantesMixin._new_credential_token = RepositorioParticipantesMixin._nuevo_credencial_token
RepositorioParticipantesMixin.log_email = RepositorioParticipantesMixin.registrar_bitacora_correo
