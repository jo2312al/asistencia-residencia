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

class RepositorioUsuariosMixin:
    """Operaciones de usuarios extraídas de la antigua clase monolítica."""

    def normalizar_rol(self, role):
        """Ejecuta la operación normalizar rol y devuelve el resultado correspondiente."""
        role_value = (role or 'guest').strip().lower()
        if role_value not in self.VALID_ROLES:
            raise ValueError("Rol invalido. Roles permitidos: adminsuperior, admin, staff, guest")
        return role_value

    def obtener_usuario(self, username):
        """Ejecuta la operación obtener usuario y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT username, password_hash, role FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()
        return user

    def agregar_usuario(self, username, password_hash, role='guest'):
        """Ejecuta la operación agregar usuario y devuelve el resultado correspondiente."""
        role_value = self.normalizar_rol(role)
        conn = self.conectar()
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, password_hash, role_value)
        )
        conn.commit()
        conn.close()

    def obtener_todos_usuarios(self):
        """Ejecuta la operación obtener todos usuarios y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        c.execute("SELECT username, role FROM users ORDER BY username")
        users = c.fetchall()
        conn.close()
        return users

    def _normalizar_campo_nombre(self, value):
        """Realiza internamente la operación normalizar campo nombre."""
        text = unicodedata.normalize("NFD", value or "")
        return "".join(char for char in text if unicodedata.category(char) != "Mn").strip().lower()

    def _normalizar_excel_encabezado(self, value):
        """Realiza internamente la operación normalizar excel encabezado."""
        text = unicodedata.normalize("NFD", str(value or ""))
        text = "".join(char for char in text if unicodedata.category(char) != "Mn").lower()
        text = "".join(char if char.isalnum() else " " for char in text)
        return " ".join(text.split())

    def _buscar_excel_columna(self, df, aliases):
        """Realiza internamente la operación buscar excel columna."""
        normalized = {self._normalizar_excel_encabezado(column): column for column in df.columns}
        for alias in aliases:
            column = normalized.get(self._normalizar_excel_encabezado(alias))
            if column is not None:
                return column
        return None

    def _fila_value_para_campo(self, row, field_name):
        """Realiza internamente la operación row value for field."""
        if field_name in row.indice:
            return row[field_name]

        normalized_field = self._normalizar_campo_nombre(field_name)
        aliases = {
            "correo": ("email", "e-mail", "correo electronico", "correo"),
            "email": ("email", "e-mail", "correo electronico", "correo"),
        }
        for alias in aliases.get(normalized_field, ()):
            for column in row.indice:
                if self._normalizar_campo_nombre(column) == alias:
                    return row[column]
        return ""

    def _leer_excel_tabla(self, file):
        """Realiza internamente la operación leer excel tabla."""
        if hasattr(file, "seek"):
            file.seek(0)
        df = pd.read_excel(file)
        if self._parece_like_evento_excel(df):
            return df

        if hasattr(file, "seek"):
            file.seek(0)
        raw_df = pd.read_excel(file, header=None)
        header_row_index = self._buscar_evento_excel_encabezado_fila(raw_df)
        if header_row_index is None:
            return df

        headers = [
            self._celda_texto(value) or f"Unnamed: {indice}"
            for indice, value in enumerate(raw_df.iloc[header_row_index].tolist())
        ]
        table_df = raw_df.iloc[header_row_index + 1:].copy()
        table_df.columns = headers
        table_df = table_df.dropna(how="all").reset_index(drop=True)
        return table_df

    def _buscar_evento_excel_encabezado_fila(self, raw_df):
        """Realiza internamente la operación buscar evento excel encabezado fila."""
        for indice, row in raw_df.head(30).iterrows():
            normalized_values = {self._normalizar_excel_encabezado(value) for value in row.tolist() if self._celda_texto(value)}
            has_project = "proyecto" in normalized_values
            has_name = any(value in normalized_values for value in ("nombre autores asesores", "nombre autores", "autores", "asesores"))
            has_control = any(
                value in normalized_values
                for value in (
                    "num control departamento",
                    "numero control departamento",
                    "num control",
                    "no control",
                    "departamento",
                )
            )
            if has_project and has_name and has_control:
                return indice
        return None

    def _celda_texto(self, value):
        """Realiza internamente la operación cell text."""
        if pd.isna(value):
            return ""
        return str(value).strip()

    def _dividir_celda_items(self, value, split_commas=False):
        """Realiza internamente la operación split cell items."""
        text = self._celda_texto(value)
        if not text:
            return []
        pattern = r"[\n;|]+"
        if split_commas:
            pattern = r"[\n;|,]+"
        return [item.strip() for item in re.split(pattern, text) if item.strip()]

    def _dividir_full_nombre(self, full_name):
        """Realiza internamente la operación split full name."""
        parts = self._celda_texto(full_name).split()
        if not parts:
            return "", "", ""
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], parts[1], ""
        return " ".join(parts[:-2]), parts[-2], parts[-1]

    def _es_placeholder_participante_nombre(self, value):
        """Realiza internamente la operación is placeholder participant name."""
        normalized = self._normalizar_excel_encabezado(value)
        return normalized in {
            "autor",
            "autores",
            "asesor",
            "asesores",
            "nombre autores asesores",
        }

    def _asesor_matricula(self, name, email, project, folio):
        """Realiza internamente la operación advisor matricula."""
        source = "|".join([
            self._normalizar_excel_encabezado(name),
            self._normalizar_excel_encabezado(email),
            self._normalizar_excel_encabezado(project),
            self._normalizar_excel_encabezado(folio),
        ])
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12].upper()
        return f"ASE-{digest}"

    def _parece_like_evento_excel(self, df):
        """Realiza internamente la operación looks like event excel."""
        return (
            self._buscar_excel_columna(df, ["Proyecto"]) is not None
            and self._buscar_excel_columna(df, ["Nombre Autores/Asesores", "Nombre Autores", "Autores", "Asesores"]) is not None
            and self._buscar_excel_columna(df, ["Num. Control/Departamento", "Numero Control Departamento", "Num Control", "Departamento"]) is not None
        )

    def _convertir_evento_excel(self, df):
        """Realiza internamente la operación convert event excel."""
        project_col = self._buscar_excel_columna(df, ["Proyecto"])
        folio_col = self._buscar_excel_columna(df, ["Folio"])
        event_col = self._buscar_excel_columna(df, ["Evento"])
        category_col = self._buscar_excel_columna(df, ["Categoria", "Categoría"])
        level_col = self._buscar_excel_columna(df, ["Nivel"])
        semester_col = self._buscar_excel_columna(df, ["Semestre"])
        career_col = self._buscar_excel_columna(df, ["Carrera"])
        name_col = self._buscar_excel_columna(df, ["Nombre Autores/Asesores", "Nombre Autores", "Nombre Asesores", "Autores", "Asesores"])
        email_col = self._buscar_excel_columna(df, ["E-mail Autores/Asesores", "Email Autores/Asesores", "Correo Autores/Asesores", "E-mail", "Email"])
        control_col = self._buscar_excel_columna(df, ["Num. Control/Departamento", "Numero Control Departamento", "Num Control", "No Control", "Departamento"])

        rows = []
        current = {
            "project": "",
            "folio": "",
            "event": "",
            "category": "",
            "level": "",
            "semester": "",
            "career": "",
            "description": "",
        }

        for _, row in df.dropna(how="all").iterrows():
            project_value = self._celda_texto(row[project_col]) if project_col else ""
            folio_value = self._celda_texto(row[folio_col]) if folio_col else ""
            is_project_header = bool(project_value and re.match(r"^\d+-\d+$", folio_value))
            if project_value and not is_project_header and not self._es_placeholder_participante_nombre(project_value):
                current["description"] = project_value

            values = {
                "project": project_value if is_project_header else "",
                "folio": self._celda_texto(row[folio_col]) if folio_col else "",
                "event": self._celda_texto(row[event_col]) if event_col else "",
                "category": self._celda_texto(row[category_col]) if category_col else "",
                "level": self._celda_texto(row[level_col]) if level_col else "",
                "semester": self._celda_texto(row[semester_col]) if semester_col else "",
                "career": self._celda_texto(row[career_col]) if career_col else "",
            }
            for key, value in values.items():
                if value:
                    current[key] = value

            names = self._dividir_celda_items(row[name_col]) if name_col else []
            emails = self._dividir_celda_items(row[email_col], split_commas=True) if email_col else []
            controls = self._dividir_celda_items(row[control_col], split_commas=True) if control_col else []

            for indice, name in enumerate(names):
                if self._es_placeholder_participante_nombre(name):
                    continue

                email = emails[indice] if indice < len(emails) else (emails[0] if len(emails) == 1 else "")
                control = controls[indice] if indice < len(controls) else (controls[0] if len(controls) == 1 else "")
                combined = self._normalizar_excel_encabezado(f"{name} {email} {control}")
                is_advisor = "asesor" in combined or (control and any(char.isalpha() for char in control) and not any(char.isdigit() for char in control))
                participant_type = "asesor" if is_advisor else "alumno"
                first_name, last_name_p, last_name_m = self._dividir_full_nombre(name)
                matricula = control
                if participant_type == "asesor":
                    matricula = self._asesor_matricula(name, email, current["project"], current["folio"])
                if not matricula:
                    prefix = "ASESOR" if participant_type == "asesor" else "SINCONTROL"
                    matricula = f"{prefix}-{uuid.uuid4().hex[:8]}"

                rows.append({
                    "first_name": first_name,
                    "last_name_p": last_name_p,
                    "last_name_m": last_name_m,
                    "matricula": matricula,
                    "carrera": current["career"] or current["level"] or "Sin carrera",
                    "project_name": current["project"],
                    "email": email,
                    "Correo": email,
                    "Email": email,
                    "participant_type": participant_type,
                    "Folio": current["folio"],
                    "Evento": current["event"],
                    "Categoria": current["category"],
                    "Nivel": current["level"],
                    "Semestre": current["semester"],
                    "Descripcion": current["description"],
                    "Departamento": control if participant_type == "asesor" else "",
                })

        if not rows:
            raise ValueError("No se encontraron autores o asesores para importar en el Excel")
        return pd.DataFrame(rows)

    def preparar_excel_importacion_tabla_datos(self, file):
        """Ejecuta la operación preparar excel importacion tabla datos y devuelve el resultado correspondiente."""
        df = self._leer_excel_tabla(file)
        required_columns = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        if all(col in df.columns for col in required_columns):
            return df, "standard"
        if self._parece_like_evento_excel(df):
            return self._convertir_evento_excel(df), "event_format"
        return df, "unknown"

    def actualizar_usuario_rol(self, username, role):
        """Ejecuta la operación actualizar usuario rol y devuelve el resultado correspondiente."""
        role_value = self.normalizar_rol(role)
        conn = self.conectar()
        c = conn.cursor()
        c.execute("UPDATE users SET role = %s WHERE username = %s", (role_value, username))
        conn.commit()
        updated = c.rowcount
        conn.close()
        return updated > 0

    def asegurar_usuario(self, username, password_hash, role='guest'):
        """Ejecuta la operación asegurar usuario y devuelve el resultado correspondiente."""
        role_value = self.normalizar_rol(role)
        conn = self.conectar()
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

# Alias temporales para compatibilidad con la API anterior.
RepositorioUsuariosMixin._find_event_excel_header_row = RepositorioUsuariosMixin._buscar_evento_excel_encabezado_fila
RepositorioUsuariosMixin._find_excel_column = RepositorioUsuariosMixin._buscar_excel_columna
RepositorioUsuariosMixin._normalize_excel_header = RepositorioUsuariosMixin._normalizar_excel_encabezado
RepositorioUsuariosMixin._normalize_field_name = RepositorioUsuariosMixin._normalizar_campo_nombre
RepositorioUsuariosMixin._read_excel_table = RepositorioUsuariosMixin._leer_excel_tabla
RepositorioUsuariosMixin.add_user = RepositorioUsuariosMixin.agregar_usuario
RepositorioUsuariosMixin.ensure_user = RepositorioUsuariosMixin.asegurar_usuario
RepositorioUsuariosMixin.get_all_users = RepositorioUsuariosMixin.obtener_todos_usuarios
RepositorioUsuariosMixin.get_user = RepositorioUsuariosMixin.obtener_usuario
RepositorioUsuariosMixin.normalize_role = RepositorioUsuariosMixin.normalizar_rol
RepositorioUsuariosMixin.prepare_excel_import_dataframe = RepositorioUsuariosMixin.preparar_excel_importacion_tabla_datos
RepositorioUsuariosMixin.update_user_role = RepositorioUsuariosMixin.actualizar_usuario_rol

# Alias temporales para compatibilidad con la API anterior.
RepositorioUsuariosMixin._advisor_matricula = RepositorioUsuariosMixin._asesor_matricula
RepositorioUsuariosMixin._cell_text = RepositorioUsuariosMixin._celda_texto
RepositorioUsuariosMixin._convert_event_excel = RepositorioUsuariosMixin._convertir_evento_excel
RepositorioUsuariosMixin._is_placeholder_participant_name = RepositorioUsuariosMixin._es_placeholder_participante_nombre
RepositorioUsuariosMixin._looks_like_event_excel = RepositorioUsuariosMixin._parece_like_evento_excel
RepositorioUsuariosMixin._row_value_for_field = RepositorioUsuariosMixin._fila_value_para_campo
RepositorioUsuariosMixin._split_cell_items = RepositorioUsuariosMixin._dividir_celda_items
RepositorioUsuariosMixin._split_full_name = RepositorioUsuariosMixin._dividir_full_nombre
