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

class RepositorioEstudiantesMixin:
    """Operaciones de estudiantes extraídas de la antigua clase monolítica."""

    def obtener_total_participantes(self, event_id=None):
        """Ejecuta la operación obtener total participantes y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        if event_id:
            c.execute("SELECT COUNT(*) FROM students WHERE event_id = %s", (event_id,))
        else:
            c.execute("SELECT COUNT(*) FROM students")
        total = c.fetchone()[0]
        conn.close()
        return total

    def obtener_total_asistencia(self, event_id=None):
        """Ejecuta la operación obtener total asistencia y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        if event_id:
            c.execute("SELECT COUNT(*) FROM attendance_events WHERE event_id = %s", (event_id,))
        else:
            c.execute("SELECT COUNT(*) FROM attendance")
        total = c.fetchone()[0]
        conn.close()
        return total

    def obtener_todos_participantes(self):
        """Ejecuta la operación obtener todos participantes y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera,
                      s.project_id, s.event_id, COALESCE(p.participant_type, 'alumno')
               FROM students s
               LEFT JOIN participants p ON p.legacy_student_id = s.id"""
        )
        students = c.fetchall()
        conn.close()
        return students

    def obtener_participante_por_matricula(self, matricula):
        """Ejecuta la operación obtener participante por matricula y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id, event_id FROM students WHERE matricula = %s", (matricula,))
        student = c.fetchone()
        conn.close()
        return student

    def obtener_participante_por_id(self, student_id):
        """Ejecuta la operación obtener participante por id y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera,
                      s.project_id, s.event_id, p.id AS participant_id, p.email, p.participant_type,
                      pr.name AS project_name
               FROM students s
               LEFT JOIN participants p ON p.legacy_student_id = s.id
               LEFT JOIN projects pr ON s.project_id = pr.id
               WHERE s.id = %s""",
            (student_id,)
        )
        student = c.fetchone()
        conn.close()
        return student

    def actualizar_participante_participante(self, student_id, first_name, last_name_p, last_name_m, matricula,
                                   carrera, project_id=None, email=None, participant_type='alumno'):
        """Ejecuta la operación actualizar participante participante y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            """UPDATE students
               SET first_name = %s, last_name_p = %s, last_name_m = %s,
                   matricula = %s, carrera = %s, project_id = %s
               WHERE id = %s""",
            (first_name, last_name_p, last_name_m, matricula, carrera, project_id or None, student_id)
        )
        full_name = f"{first_name} {last_name_p} {last_name_m}".strip()
        c.execute(
            """UPDATE participants
               SET full_name = %s, email = %s, participant_type = %s,
                   project_id = %s, updated_at = %s
               WHERE legacy_student_id = %s""",
            (full_name, email or None, participant_type or 'alumno', project_id or None, datetime.now(), student_id)
        )
        conn.commit()
        updated = c.rowcount
        conn.close()
        return updated > 0

    def importar_participantes_desde_excel(self, file, project_id, event_id=None):
        """Valida el archivo y coordina la importación transaccional de participantes."""
        df, _ = self.preparar_excel_importacion_tabla_datos(file)
        requeridas = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        if not all(columna in df.columns for columna in requeridas):
            raise ValueError(
                "El Excel debe contener las columnas: first_name, last_name_p, last_name_m, "
                "matricula, carrera o el formato oficial de evento"
            )
        contexto = self._preparar_contexto_importacion(df, project_id, event_id)
        exitos, errores, proyectos = self._procesar_filas_importacion(
            df, project_id, event_id, contexto
        )
        resumen = f" Proyectos asociados/creados: {len(proyectos)}." if contexto["project_column"] and proyectos else ""
        if errores:
            return f"Procesados {exitos} estudiantes.{resumen} Errores: {', '.join(errores)}"
        return f"Se procesaron {exitos} estudiantes correctamente.{resumen}"

    def _preparar_contexto_importacion(self, df, project_id, event_id):
        """Valida evento, proyecto y columnas dinámicas antes de abrir el ciclo por filas."""
        conn = self.conectar()
        c = conn.cursor()
        errors = []
        success_count = 0
        imported_project_ids = set()
        project_fields = []
        event_fields = []
        project_column = next((col for col in ('project_id', 'project_name', 'project', 'proyecto') if col in df.columns), None)
        project_lookup = {}
        project_fields_cache = {}

        if event_id:
            c.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if not c.fetchone():
                conn.close()
                raise ValueError("Evento no encontrado")
            event_fields = self.obtener_evento_campos(event_id)

        if project_id:
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                raise ValueError("Proyecto no encontrado")
            project_fields = self.obtener_proyecto_campos(project_id)
            project_fields_cache[int(project_id)] = project_fields

        if project_column and project_column != 'project_id':
            project_names = []
            for raw_project in df[project_column].dropna().tolist():
                project_name = str(raw_project).strip()
                if project_name and project_name not in project_lookup:
                    project_names.append(project_name)
                    project_lookup[project_name] = None
            for project_name in project_names:
                project_lookup[project_name] = self._obtener_o_crear_proyecto_por_nombre_con_cursor(c, project_name, event_id)
            conn.commit()

        required_dynamic_columns = [field[2] for field in project_fields if field[4]]
        required_dynamic_columns += [
            field[2]
            for field in event_fields
            if field[4] and self._normalizar_campo_nombre(field[2]) not in self.BASE_REGISTRATION_FIELD_NAMES
        ]
        missing_dynamic_columns = [
            name
            for name in required_dynamic_columns
            if self._buscar_excel_columna(df, [name]) is None
            and self._normalizar_campo_nombre(name) not in ("correo", "email")
        ]
        if missing_dynamic_columns:
            conn.close()
            raise ValueError(
                "El Excel debe contener las columnas configuradas como obligatorias: "
                + ", ".join(missing_dynamic_columns)
            )
        return {
            "conn": conn, "cursor": c, "project_fields": project_fields,
            "event_fields": event_fields, "project_column": project_column,
            "project_lookup": project_lookup, "project_fields_cache": project_fields_cache,
        }

    def _procesar_filas_importacion(self, df, project_id, event_id, contexto):
        """Procesa cada fila de forma aislada y acumula éxitos, errores y proyectos."""
        conn, c = contexto["conn"], contexto["cursor"]
        project_fields, event_fields = contexto["project_fields"], contexto["event_fields"]
        project_column, project_lookup = contexto["project_column"], contexto["project_lookup"]
        project_fields_cache = contexto["project_fields_cache"]
        errors, success_count, imported_project_ids = [], 0, set()
        for indice, row in df.iterrows():
            student_id = str(uuid.uuid4())
            matricula = str(row['matricula'])
            try:
                participant_type = str(row['participant_type']).strip().lower() if 'participant_type' in df.columns and not pd.isna(row['participant_type']) else 'alumno'
                email_value = str(row['email']).strip() if 'email' in df.columns and not pd.isna(row['email']) else None
                row_project_id = project_id
                if project_column and not pd.isna(row[project_column]):
                    raw_project = str(row[project_column]).strip()
                    if raw_project:
                        if project_column == 'project_id' and raw_project.isdigit():
                            row_project_id = int(raw_project)
                        else:
                            row_project_id = project_lookup.get(raw_project)
                            if row_project_id is None:
                                row_project_id = self._obtener_o_crear_proyecto_por_nombre_con_cursor(c, raw_project, event_id)
                                project_lookup[raw_project] = row_project_id
                if row_project_id:
                    imported_project_ids.add(row_project_id)
                row_project_fields = project_fields
                if row_project_id and row_project_id != project_id:
                    if row_project_id not in project_fields_cache:
                        project_fields_cache[row_project_id] = self.obtener_proyecto_campos(row_project_id)
                    row_project_fields = project_fields_cache[row_project_id]
                dynamic_values = {}
                for field in row_project_fields:
                    field_id = field[0]
                    field_name = field[2]
                    is_required = bool(field[4])
                    raw_value = self._fila_value_para_campo(row, field_name)
                    value = "" if pd.isna(raw_value) else str(raw_value).strip()
                    if is_required and not value:
                        raise ValueError(f"Falta {field_name}")
                    if value:
                        dynamic_values[field_id] = value

                event_dynamic_values = {}
                for field in event_fields:
                    field_id = field[0]
                    field_name = field[2]
                    if self._normalizar_campo_nombre(field_name) in self.BASE_REGISTRATION_FIELD_NAMES:
                        continue
                    is_required = bool(field[4])
                    raw_value = self._fila_value_para_campo(row, field_name)
                    value = "" if pd.isna(raw_value) else str(raw_value).strip()
                    if is_required and not value:
                        raise ValueError(f"Falta {field_name}")
                    if value:
                        event_dynamic_values[field_id] = value
                    if self._normalizar_campo_nombre(field_name) in ('correo', 'email') and value:
                        email_value = value

                c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                          (student_id, str(row['first_name']), str(row['last_name_p']), str(row['last_name_m']),
                           matricula, str(row['carrera']), event_id, row_project_id))
                participant_id = self._asegurar_participante_para_participante(
                    c,
                    student_id,
                    str(row['first_name']),
                    str(row['last_name_p']),
                    str(row['last_name_m']),
                    row_project_id,
                    event_id,
                    email_value,
                    participant_type
                )
                self._asegurar_credencial_para_participante(c, participant_id)
                self._guardar_participante_campo_valores(c, participant_id, dynamic_values)
                if event_dynamic_values:
                    now = datetime.now()
                    for field_id, value in event_dynamic_values.items():
                        c.execute(
                            """INSERT INTO participant_event_field_values
                               (participant_id, field_id, value, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (participant_id, field_id, value, now, now)
                        )
                conn.commit()
                success_count += 1
            except mysql.connector.Error as e:
                conn.rollback()
                if e.errno == 1062:
                    errors.append(f"Matrícula {matricula} ya registrada")
                else:
                    errors.append(f"Error en matrícula {matricula}: {str(e)}")
            except Exception as e:
                conn.rollback()
                errors.append(f"Error en matrícula {matricula}: {str(e)}")
        conn.close()
        return success_count, errors, imported_project_ids

    def _participante_filtrar_condiciones(self, matricula_search='', apellido_p_search='', project_id_filter='', event_id_filter=''):
        """Realiza internamente la operación participante filtrar condiciones."""
        clauses = ["1=1"]
        params = []
        if matricula_search:
            clauses.append("s.matricula LIKE %s")
            params.append(f"%{matricula_search}%")
        if apellido_p_search:
            clauses.append("s.last_name_p LIKE %s")
            params.append(f"%{apellido_p_search}%")
        if project_id_filter:
            clauses.append("s.project_id = %s")
            params.append(project_id_filter)
        if event_id_filter:
            clauses.append("s.event_id = %s")
            params.append(event_id_filter)
        return " AND ".join(clauses), params

    def contar_participantes_filtrados(self, matricula_search='', apellido_p_search='', project_id_filter='', event_id_filter=''):
        """Ejecuta la operación contar participantes filtrados y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        where_clause, params = self._participante_filtrar_condiciones(
            matricula_search,
            apellido_p_search,
            project_id_filter,
            event_id_filter,
        )
        c.execute(f"SELECT COUNT(*) FROM students s WHERE {where_clause}", params)
        total = c.fetchone()[0]
        conn.close()
        return total

    def obtener_todos_participantes_filtrados(
        self,
        matricula_search='',
        apellido_p_search='',
        project_id_filter='',
        event_id_filter='',
        sort_by='proyecto',
        sort_dir='asc',
        limit=None,
        offset=0,
    ):
        """Ejecuta la operación obtener todos participantes filtrados y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            query, params = self._filtered_students_query(
                matricula_search, apellido_p_search, project_id_filter, event_id_filter, sort_by, sort_dir, limit, offset
            )
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchall()
        finally:
            conn.close()

    def obtener_participantes_filtrados_pagina(
        self,
        matricula_search='',
        apellido_p_search='',
        project_id_filter='',
        event_id_filter='',
        sort_by='proyecto',
        sort_dir='asc',
        limit=10,
        offset=0,
    ):
        """Ejecuta la operación obtener participantes filtrados pagina y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            query, params = self._filtered_students_query(
                matricula_search, apellido_p_search, project_id_filter, event_id_filter, sort_by, sort_dir, limit, offset, True
            )
            c = conn.cursor()
            c.execute(query, params)
            rows = c.fetchall()
            total = rows[0][-1] if rows else 0
            return rows, total
        finally:
            conn.close()

    def _filtered_students_query(self, matricula, apellido, project_id, event_id, sort_by, sort_dir, limit, offset, include_total=False):
        """Realiza internamente la operación filtered students query."""
        where_clause, params = self._participante_filtrar_condiciones(matricula, apellido, project_id, event_id)
        query = self._filtered_students_select(where_clause, sort_by, sort_dir, include_total)
        if limit is not None:
            query += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset or 0)])
        return query, params

    def _filtered_students_select(self, where_clause, sort_by, sort_dir, include_total=False):
        """Realiza internamente la operación filtered students select."""
        sort_expression = self._participante_orden_expression(sort_by)
        direction = 'DESC' if str(sort_dir).lower() == 'desc' else 'ASC'
        total_column = ", COUNT(*) OVER() AS total_count" if include_total else ""
        return f"""
            SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera,
                   s.project_id, s.event_id, COALESCE(p.participant_type, 'alumno'), pr.name AS project_name,
                   cr.token AS credential_token, cr.qr_path AS credential_qr_path{total_column}
            FROM students s
            LEFT JOIN participants p ON p.legacy_student_id = s.id
            LEFT JOIN projects pr ON s.project_id = pr.id
            LEFT JOIN ({self._ultimo_credenciales_select()}) cr ON cr.participant_id = p.id
            WHERE {where_clause}
            ORDER BY {sort_expression} {direction}, s.last_name_p ASC, s.first_name ASC, s.matricula ASC
        """

    def _ultimo_credenciales_select(self):
        """Realiza internamente la operación latest credentials select."""
        return """
            SELECT c.participant_id, c.token, c.qr_path
            FROM credentials c
            JOIN (
                SELECT participant_id, MAX(created_at) AS created_at
                FROM credentials
                GROUP BY participant_id
            ) latest ON latest.participant_id = c.participant_id
                   AND latest.created_at = c.created_at
        """

    def _participante_orden_expression(self, sort_by):
        """Realiza internamente la operación student sort expression."""
        sort_columns = {
            'matricula': 's.matricula',
            'nombre': 's.first_name',
            'apellido_p': 's.last_name_p',
            'apellido_m': 's.last_name_m',
            'carrera': 's.carrera',
            'tipo': "COALESCE(p.participant_type, 'alumno')",
            'proyecto': 'project_name',
        }
        return sort_columns.get(sort_by, 'project_name')

# Alias temporales para compatibilidad con la API anterior.
RepositorioEstudiantesMixin._student_filter_where = RepositorioEstudiantesMixin._participante_filtrar_condiciones
RepositorioEstudiantesMixin.count_students_filtered = RepositorioEstudiantesMixin.contar_participantes_filtrados
RepositorioEstudiantesMixin.get_all_students = RepositorioEstudiantesMixin.obtener_todos_participantes
RepositorioEstudiantesMixin.get_all_students_filtered = RepositorioEstudiantesMixin.obtener_todos_participantes_filtrados
RepositorioEstudiantesMixin.get_student_by_id = RepositorioEstudiantesMixin.obtener_participante_por_id
RepositorioEstudiantesMixin.get_student_by_matricula = RepositorioEstudiantesMixin.obtener_participante_por_matricula
RepositorioEstudiantesMixin.get_students_filtered_page = RepositorioEstudiantesMixin.obtener_participantes_filtrados_pagina
RepositorioEstudiantesMixin.get_total_attendance = RepositorioEstudiantesMixin.obtener_total_asistencia
RepositorioEstudiantesMixin.get_total_students = RepositorioEstudiantesMixin.obtener_total_participantes
RepositorioEstudiantesMixin.update_student_participant = RepositorioEstudiantesMixin.actualizar_participante_participante
RepositorioEstudiantesMixin.upload_students_from_excel = RepositorioEstudiantesMixin.importar_participantes_desde_excel

# Alias temporales para compatibilidad con la API anterior.
RepositorioEstudiantesMixin._latest_credentials_select = RepositorioEstudiantesMixin._ultimo_credenciales_select
RepositorioEstudiantesMixin._student_sort_expression = RepositorioEstudiantesMixin._participante_orden_expression
