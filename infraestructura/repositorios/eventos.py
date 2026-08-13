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

class RepositorioEventosMixin:
    """Operaciones de eventos extraídas de la antigua clase monolítica."""

    def obtener_todos_proyectos(self):
        """Ejecuta la operación obtener todos proyectos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT p.id, p.name, p.description, e.name
               FROM projects p
               LEFT JOIN events e ON p.event_id = e.id"""
        )
        projects = c.fetchall()
        conn.close()
        return projects

    def obtener_proyectos_por_evento(self, event_id):
        """Ejecuta la operación obtener proyectos por evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, name, description, event_id
               FROM projects
               WHERE event_id = %s
               ORDER BY name""",
            (event_id,)
        )
        projects = c.fetchall()
        conn.close()
        return projects

    def obtener_proyecto(self, project_id):
        """Ejecuta la operación obtener proyecto y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT p.id, p.name, p.description, e.name, p.event_id
               FROM projects p
               LEFT JOIN events e ON p.event_id = e.id
               WHERE p.id = %s""",
            (project_id,)
        )
        project = c.fetchone()
        conn.close()
        return project

    def agregar_proyecto(self, name, description=None, event_id=None):
        """Ejecuta la operación agregar proyecto y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            "INSERT INTO projects (name, description, event_id) VALUES (%s, %s, %s)",
            (name, description, event_id or None)
        )
        conn.commit()
        project_id = c.lastrowid
        conn.close()
        return project_id

    def _obtener_o_crear_proyecto_por_nombre_con_cursor(self, cursor, name, event_id):
        """Realiza internamente la operación obtener o crear proyecto por nombre con cursor."""
        project_name = (name or "").strip()
        if not project_name:
            return None

        cursor.execute(
            "SELECT id FROM projects WHERE name = %s AND (event_id = %s OR event_id IS NULL) ORDER BY event_id IS NULL LIMIT 1",
            (project_name, event_id)
        )
        existing = cursor.fetchone()
        if existing:
            project_id = existing[0]
            cursor.execute("UPDATE projects SET event_id = %s WHERE id = %s AND event_id IS NULL", (event_id, project_id))
            return project_id

        try:
            cursor.execute(
                "INSERT INTO projects (name, description, event_id) VALUES (%s, %s, %s)",
                (project_name, None, event_id)
            )
            return cursor.lastrowid
        except mysql.connector.Error as e:
            if e.errno != 1062:
                raise
            cursor.execute("SELECT id FROM projects WHERE name = %s LIMIT 1", (project_name,))
            row = cursor.fetchone()
            project_id = row[0] if row else None
            if project_id:
                cursor.execute("UPDATE projects SET event_id = %s WHERE id = %s AND event_id IS NULL", (event_id, project_id))
            return project_id

    def obtener_o_crear_proyecto_por_nombre(self, name, event_id):
        """Ejecuta la operación obtener o crear proyecto por nombre y devuelve el resultado correspondiente."""
        project_name = (name or "").strip()
        if not project_name:
            return None

        conn = self.conectar()
        c = conn.cursor()
        project_id = self._obtener_o_crear_proyecto_por_nombre_con_cursor(c, project_name, event_id)
        conn.commit()
        conn.close()
        return project_id

    def obtener_todos_eventos(self):
        """Ejecuta la operación obtener todos eventos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, name, description, start_datetime, end_datetime, location,
                      status, event_type, duplicate_policy
               FROM events
               ORDER BY COALESCE(start_datetime, created_at) DESC, id DESC"""
        )
        events = c.fetchall()
        conn.close()
        return events

    def obtener_evento(self, event_id):
        """Ejecuta la operación obtener evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, name, description, start_datetime, end_datetime, location, status, event_type, duplicate_policy
               FROM events WHERE id = %s""",
            (event_id,)
        )
        event = c.fetchone()
        conn.close()
        return event

    def actualizar_evento(self, event_id, name, description=None, start_datetime=None, end_datetime=None,
                     location=None, status='active', event_type='general', duplicate_policy='once_per_day'):
        """Ejecuta la operación actualizar evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """UPDATE events
               SET name = %s, description = %s, start_datetime = %s, end_datetime = %s,
                   location = %s, status = %s, event_type = %s, duplicate_policy = %s
               WHERE id = %s""",
            (
                name,
                description,
                start_datetime or None,
                end_datetime or None,
                location,
                status or 'active',
                event_type or 'general',
                duplicate_policy or 'once_per_day',
                event_id,
            )
        )
        conn.commit()
        updated = c.rowcount
        conn.close()
        return updated > 0

    def obtener_ultimo_evento(self):
        """Ejecuta la operación obtener ultimo evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, name, description, start_datetime, end_datetime, location, status, event_type, duplicate_policy
               FROM events
               ORDER BY COALESCE(start_datetime, created_at) DESC, id DESC
               LIMIT 1"""
        )
        event = c.fetchone()
        conn.close()
        return event

    def obtener_activos_eventos(self):
        """Ejecuta la operación obtener activos eventos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, name, event_type, location
               FROM events
               WHERE status = 'active'
               ORDER BY COALESCE(start_datetime, created_at) DESC, id DESC"""
        )
        events = c.fetchall()
        conn.close()
        return events

    def agregar_evento(self, name, description=None, start_datetime=None, end_datetime=None,
                  location=None, status='active', event_type='general', duplicate_policy='once_per_day'):
        """Ejecuta la operación agregar evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """INSERT INTO events
               (name, description, start_datetime, end_datetime, location, status, event_type, duplicate_policy, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                name,
                description,
                start_datetime or None,
                end_datetime or None,
                location,
                status or 'active',
                event_type or 'general',
                duplicate_policy or 'once_per_day',
                datetime.now(),
            )
        )
        conn.commit()
        event_id = c.lastrowid
        conn.close()
        return event_id

    def obtener_proyecto_campos(self, project_id):
        """Ejecuta la operación obtener proyecto campos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        type_column = 'field_type'
        if not self._columna_exists(c, 'project_fields', 'field_type') and self._columna_exists(c, 'project_fields', 'type'):
            type_column = 'type'
        c.execute(
            f"""SELECT id, project_id, name, {type_column}, is_required, display_order
               FROM project_fields
               WHERE project_id = %s
               ORDER BY display_order, id""",
            (project_id,)
        )
        fields = c.fetchall()
        conn.close()
        return fields

    def obtener_proyecto_campos_como_diccionarios(self, project_id):
        """Ejecuta la operación obtener proyecto campos como diccionarios y devuelve el resultado correspondiente."""
        return [
            {
                "id": field[0],
                "project_id": field[1],
                "name": field[2],
                "field_type": field[3],
                "is_required": bool(field[4]),
                "display_order": field[5],
            }
            for field in self.obtener_proyecto_campos(project_id)
        ]

    def obtener_evento_campos(self, event_id):
        """Ejecuta la operación obtener evento campos y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT id, event_id, name, field_type, is_required, display_order
               FROM event_fields
               WHERE event_id = %s
               ORDER BY display_order, id""",
            (event_id,)
        )
        fields = c.fetchall()
        conn.close()
        return fields

    def obtener_evento_campos_como_diccionarios(self, event_id):
        """Ejecuta la operación obtener evento campos como diccionarios y devuelve el resultado correspondiente."""
        return [
            {
                "id": field[0],
                "event_id": field[1],
                "name": field[2],
                "field_type": field[3],
                "is_required": bool(field[4]),
                "display_order": field[5],
            }
            for field in self.obtener_evento_campos(event_id)
        ]

    def agregar_evento_campo(self, event_id, name, field_type='text', is_required=False, display_order=0):
        """Ejecuta la operación agregar evento campo y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT id FROM events WHERE id = %s", (event_id,))
        if not c.fetchone():
            conn.close()
            raise ValueError("Evento no encontrado")
        c.execute(
            """INSERT INTO event_fields
               (event_id, name, field_type, is_required, display_order, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (event_id, name, field_type or 'text', bool(is_required), display_order or 0, datetime.now())
        )
        conn.commit()
        field_id = c.lastrowid
        conn.close()
        return field_id

    def eliminar_evento_campo(self, field_id):
        """Ejecuta la operación eliminar evento campo y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("DELETE FROM participant_event_field_values WHERE field_id = %s", (field_id,))
        c.execute("DELETE FROM event_fields WHERE id = %s", (field_id,))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0

    def actualizar_evento_reglas(self, event_id, duplicate_policy, status=None):
        """Ejecuta la operación actualizar evento reglas y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """UPDATE events
               SET duplicate_policy = %s, status = COALESCE(%s, status)
               WHERE id = %s""",
            (duplicate_policy or 'once_per_day', status or None, event_id)
        )
        conn.commit()
        updated = c.rowcount
        conn.close()
        return updated > 0

    def guardar_participante_evento_campo_valores(self, participant_id, field_values):
        """Ejecuta la operación guardar participante evento campo valores y devuelve el resultado correspondiente."""
        if not participant_id or not field_values:
            return

        conn = self.conectar()
        c = conn.cursor()
        now = datetime.now()
        for field_id, value in field_values.items():
            c.execute(
                """SELECT id FROM participant_event_field_values
                   WHERE participant_id = %s AND field_id = %s""",
                (participant_id, field_id)
            )
            existing = c.fetchone()
            if existing:
                c.execute(
                    """UPDATE participant_event_field_values
                       SET value = %s, updated_at = %s
                       WHERE id = %s""",
                    (value, now, existing[0])
                )
            else:
                c.execute(
                    """INSERT INTO participant_event_field_values
                       (participant_id, field_id, value, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (participant_id, field_id, value, now, now)
                )
        conn.commit()
        conn.close()

    def agregar_proyecto_campo(self, project_id, name, field_type='text', is_required=False, display_order=0):
        """Ejecuta la operación agregar proyecto campo y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
        if not c.fetchone():
            conn.close()
            raise ValueError("Proyecto no encontrado")

        type_column = 'field_type'
        if not self._columna_exists(c, 'project_fields', 'field_type') and self._columna_exists(c, 'project_fields', 'type'):
            type_column = 'type'
        c.execute(
            f"""INSERT INTO project_fields
                (project_id, name, {type_column}, is_required, display_order, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)""",
            (project_id, name, field_type, bool(is_required), display_order or 0, datetime.now())
        )
        conn.commit()
        field_id = c.lastrowid
        conn.close()
        return field_id

    def eliminar_proyecto_campo(self, field_id):
        """Ejecuta la operación eliminar proyecto campo y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("DELETE FROM project_fields WHERE id = %s", (field_id,))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0

# Alias temporales para compatibilidad con la API anterior.
RepositorioEventosMixin._get_or_create_project_by_name_with_cursor = RepositorioEventosMixin._obtener_o_crear_proyecto_por_nombre_con_cursor
RepositorioEventosMixin.add_event = RepositorioEventosMixin.agregar_evento
RepositorioEventosMixin.add_event_field = RepositorioEventosMixin.agregar_evento_campo
RepositorioEventosMixin.add_project = RepositorioEventosMixin.agregar_proyecto
RepositorioEventosMixin.add_project_field = RepositorioEventosMixin.agregar_proyecto_campo
RepositorioEventosMixin.delete_event_field = RepositorioEventosMixin.eliminar_evento_campo
RepositorioEventosMixin.delete_project_field = RepositorioEventosMixin.eliminar_proyecto_campo
RepositorioEventosMixin.get_active_events = RepositorioEventosMixin.obtener_activos_eventos
RepositorioEventosMixin.get_all_events = RepositorioEventosMixin.obtener_todos_eventos
RepositorioEventosMixin.get_all_projects = RepositorioEventosMixin.obtener_todos_proyectos
RepositorioEventosMixin.get_event = RepositorioEventosMixin.obtener_evento
RepositorioEventosMixin.get_event_fields = RepositorioEventosMixin.obtener_evento_campos
RepositorioEventosMixin.get_event_fields_as_dicts = RepositorioEventosMixin.obtener_evento_campos_como_diccionarios
RepositorioEventosMixin.get_latest_event = RepositorioEventosMixin.obtener_ultimo_evento
RepositorioEventosMixin.get_or_create_project_by_name = RepositorioEventosMixin.obtener_o_crear_proyecto_por_nombre
RepositorioEventosMixin.get_project = RepositorioEventosMixin.obtener_proyecto
RepositorioEventosMixin.get_project_fields = RepositorioEventosMixin.obtener_proyecto_campos
RepositorioEventosMixin.get_project_fields_as_dicts = RepositorioEventosMixin.obtener_proyecto_campos_como_diccionarios
RepositorioEventosMixin.get_projects_by_event = RepositorioEventosMixin.obtener_proyectos_por_evento
RepositorioEventosMixin.save_participant_event_field_values = RepositorioEventosMixin.guardar_participante_evento_campo_valores
RepositorioEventosMixin.update_event = RepositorioEventosMixin.actualizar_evento
RepositorioEventosMixin.update_event_rules = RepositorioEventosMixin.actualizar_evento_reglas
