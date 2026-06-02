import mysql.connector
from mysql.connector import Error
import pandas as pd
import uuid
import os
import re
import hashlib
import secrets
import unicodedata
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
import xlsxwriter
from werkzeug.security import generate_password_hash

class DatabaseManager:
    VALID_ROLES = {'admin', 'staff', 'guest'}
    BASE_REGISTRATION_FIELD_NAMES = {'nombre', 'apellido paterno', 'apellido materno', 'matricula', 'carrera'}

    def __init__(self, db_config):
        self.db_config = db_config
        self.init_db()

    def init_db(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(50) PRIMARY KEY,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'guest')''')
        c.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            event_id INT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            description TEXT,
            start_datetime DATETIME NULL,
            end_datetime DATETIME NULL,
            location VARCHAR(255),
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            event_type VARCHAR(50) NOT NULL DEFAULT 'general',
            duplicate_policy VARCHAR(50) NOT NULL DEFAULT 'once_per_day',
            created_at DATETIME NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS students (
            id VARCHAR(36) PRIMARY KEY,
            first_name VARCHAR(50) NOT NULL,
            last_name_p VARCHAR(50) NOT NULL,
            last_name_m VARCHAR(50) NOT NULL,
            matricula VARCHAR(20) UNIQUE NOT NULL,
            carrera VARCHAR(100) NOT NULL,
            event_id INT NULL,
            project_id INT,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (project_id) REFERENCES projects(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id INT AUTO_INCREMENT PRIMARY KEY,
            student_id VARCHAR(36),
            timestamp DATETIME,
            FOREIGN KEY (student_id) REFERENCES students(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS project_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            project_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            field_type VARCHAR(50) NOT NULL DEFAULT 'text',
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            display_order INT NOT NULL DEFAULT 0,
            created_at DATETIME NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS event_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            field_type VARCHAR(50) NOT NULL DEFAULT 'text',
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            display_order INT NOT NULL DEFAULT 0,
            created_at DATETIME NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS participants (
            id VARCHAR(36) PRIMARY KEY,
            full_name VARCHAR(150) NOT NULL,
            email VARCHAR(255),
            phone VARCHAR(50),
            participant_type VARCHAR(30) NOT NULL DEFAULT 'alumno',
            event_id INT NULL,
            project_id INT,
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            legacy_student_id VARCHAR(36) UNIQUE,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (legacy_student_id) REFERENCES students(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS participant_field_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            field_id INT NOT NULL,
            value TEXT,
            created_at DATETIME NULL,
            updated_at DATETIME NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (field_id) REFERENCES project_fields(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS participant_event_field_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            field_id INT NOT NULL,
            value TEXT,
            created_at DATETIME NULL,
            updated_at DATETIME NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (field_id) REFERENCES event_fields(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS credentials (
            id VARCHAR(36) PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            token VARCHAR(80) UNIQUE NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            qr_path VARCHAR(255),
            sent_status VARCHAR(30) NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS email_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT NULL,
            recipient VARCHAR(255) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            status VARCHAR(30) NOT NULL,
            error TEXT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS attendance_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            credential_id VARCHAR(36),
            legacy_attendance_id INT,
            event_type VARCHAR(50) NOT NULL DEFAULT 'entrada',
            timestamp DATETIME NOT NULL,
            event_id INT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (credential_id) REFERENCES credentials(id),
            FOREIGN KEY (legacy_attendance_id) REFERENCES attendance(id),
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS event_templates (
            event_id INT PRIMARY KEY,
            email_subject VARCHAR(255),
            email_body TEXT,
            credential_style VARCHAR(50) NOT NULL DEFAULT 'standard',
            logo_filename VARCHAR(255),
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_event_permissions (
            username VARCHAR(50) NOT NULL,
            event_id INT NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (username, event_id),
            FOREIGN KEY (username) REFERENCES users(username),
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        c.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'users'
            AND COLUMN_NAME = 'role'
        """, (self.db_config['database'],))
        has_role_column = c.fetchone()[0] > 0
        if not has_role_column:
            c.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'guest'")
        self._ensure_column(c, 'events', 'description', 'TEXT')
        self._ensure_column(c, 'events', 'start_datetime', 'DATETIME NULL')
        self._ensure_column(c, 'events', 'end_datetime', 'DATETIME NULL')
        self._ensure_column(c, 'events', 'location', 'VARCHAR(255)')
        self._ensure_column(c, 'events', 'status', "VARCHAR(30) NOT NULL DEFAULT 'active'")
        self._ensure_column(c, 'events', 'event_type', "VARCHAR(50) NOT NULL DEFAULT 'general'")
        self._ensure_column(c, 'events', 'duplicate_policy', "VARCHAR(50) NOT NULL DEFAULT 'once_per_day'")
        self._ensure_column(c, 'events', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'projects', 'event_id', 'INT NULL')
        self._ensure_project_event_unique_index(c)
        self._ensure_column(c, 'students', 'event_id', 'INT NULL')
        self._ensure_column(c, 'participants', 'event_id', 'INT NULL')
        self._ensure_column(c, 'participants', 'legacy_student_id', 'VARCHAR(36)')
        self._ensure_column(c, 'participants', 'participant_type', "VARCHAR(30) NOT NULL DEFAULT 'alumno'")
        self._ensure_column(c, 'project_fields', 'field_type', "VARCHAR(50) NOT NULL DEFAULT 'text'")
        self._ensure_column(c, 'project_fields', 'display_order', 'INT NOT NULL DEFAULT 0')
        self._ensure_column(c, 'project_fields', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'participant_field_values', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'participant_field_values', 'updated_at', 'DATETIME NULL')
        self._ensure_column(c, 'participant_event_field_values', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'participant_event_field_values', 'updated_at', 'DATETIME NULL')
        self._ensure_column(c, 'participants', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'participants', 'updated_at', 'DATETIME NULL')
        self._ensure_column(c, 'credentials', 'qr_path', 'VARCHAR(255)')
        self._ensure_column(c, 'credentials', 'sent_status', "VARCHAR(30) NOT NULL DEFAULT 'pending'")
        self._ensure_column(c, 'credentials', 'created_at', 'DATETIME NULL')
        self._ensure_column(c, 'credentials', 'updated_at', 'DATETIME NULL')
        self._ensure_column(c, 'attendance_events', 'credential_id', 'VARCHAR(36)')
        self._ensure_column(c, 'attendance_events', 'legacy_attendance_id', 'INT')
        self._ensure_column(c, 'attendance_events', 'event_id', 'INT NULL')
        conn.commit()
        conn.close()

    def _ensure_column(self, cursor, table_name, column_name, definition):
        if self._column_exists(cursor, table_name, column_name):
            return
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _ensure_project_event_unique_index(self, cursor):
        cursor.execute("""
            SELECT INDEX_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'projects'
              AND NON_UNIQUE = 0
              AND INDEX_NAME <> 'PRIMARY'
            GROUP BY INDEX_NAME
            HAVING GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) = 'name'
        """, (self.db_config['database'],))
        for (index_name,) in cursor.fetchall():
            cursor.execute(f"ALTER TABLE projects DROP INDEX `{index_name}`")

        cursor.execute("""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'projects'
              AND INDEX_NAME = 'uq_projects_name_event'
        """, (self.db_config['database'],))
        if not cursor.fetchone()[0]:
            cursor.execute("CREATE UNIQUE INDEX uq_projects_name_event ON projects (name, event_id)")

    def _column_exists(self, cursor, table_name, column_name):
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = %s
            AND COLUMN_NAME = %s
        """, (self.db_config['database'], table_name, column_name))
        return cursor.fetchone()[0] > 0

    def _get_column_data_type(self, cursor, table_name, column_name):
        cursor.execute("""
            SELECT DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = %s
            AND COLUMN_NAME = %s
        """, (self.db_config['database'], table_name, column_name))
        row = cursor.fetchone()
        return row[0] if row else None

    def normalize_role(self, role):
        role_value = (role or 'guest').strip().lower()
        if role_value not in self.VALID_ROLES:
            raise ValueError("Rol invalido. Roles permitidos: admin, staff, guest")
        return role_value

    def get_user(self, username):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT username, password_hash, role FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()
        return user

    def add_user(self, username, password_hash, role='guest'):
        role_value = self.normalize_role(role)
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, password_hash, role_value)
        )
        conn.commit()
        conn.close()

    def get_all_users(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT username, role FROM users ORDER BY username")
        users = c.fetchall()
        conn.close()
        return users

    def _normalize_field_name(self, value):
        text = unicodedata.normalize("NFD", value or "")
        return "".join(char for char in text if unicodedata.category(char) != "Mn").strip().lower()

    def _normalize_excel_header(self, value):
        text = unicodedata.normalize("NFD", str(value or ""))
        text = "".join(char for char in text if unicodedata.category(char) != "Mn").lower()
        text = "".join(char if char.isalnum() else " " for char in text)
        return " ".join(text.split())

    def _find_excel_column(self, df, aliases):
        normalized = {self._normalize_excel_header(column): column for column in df.columns}
        for alias in aliases:
            column = normalized.get(self._normalize_excel_header(alias))
            if column is not None:
                return column
        return None

    def _row_value_for_field(self, row, field_name):
        if field_name in row.index:
            return row[field_name]

        normalized_field = self._normalize_field_name(field_name)
        aliases = {
            "correo": ("email", "e-mail", "correo electronico", "correo"),
            "email": ("email", "e-mail", "correo electronico", "correo"),
        }
        for alias in aliases.get(normalized_field, ()):
            for column in row.index:
                if self._normalize_field_name(column) == alias:
                    return row[column]
        return ""

    def _read_excel_table(self, file):
        if hasattr(file, "seek"):
            file.seek(0)
        df = pd.read_excel(file)
        if self._looks_like_event_excel(df):
            return df

        if hasattr(file, "seek"):
            file.seek(0)
        raw_df = pd.read_excel(file, header=None)
        header_row_index = self._find_event_excel_header_row(raw_df)
        if header_row_index is None:
            return df

        headers = [
            self._cell_text(value) or f"Unnamed: {index}"
            for index, value in enumerate(raw_df.iloc[header_row_index].tolist())
        ]
        table_df = raw_df.iloc[header_row_index + 1:].copy()
        table_df.columns = headers
        table_df = table_df.dropna(how="all").reset_index(drop=True)
        return table_df

    def _find_event_excel_header_row(self, raw_df):
        for index, row in raw_df.head(30).iterrows():
            normalized_values = {self._normalize_excel_header(value) for value in row.tolist() if self._cell_text(value)}
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
                return index
        return None

    def _cell_text(self, value):
        if pd.isna(value):
            return ""
        return str(value).strip()

    def _split_cell_items(self, value, split_commas=False):
        text = self._cell_text(value)
        if not text:
            return []
        pattern = r"[\n;|]+"
        if split_commas:
            pattern = r"[\n;|,]+"
        return [item.strip() for item in re.split(pattern, text) if item.strip()]

    def _split_full_name(self, full_name):
        parts = self._cell_text(full_name).split()
        if not parts:
            return "", "", ""
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], parts[1], ""
        return " ".join(parts[:-2]), parts[-2], parts[-1]

    def _is_placeholder_participant_name(self, value):
        normalized = self._normalize_excel_header(value)
        return normalized in {
            "autor",
            "autores",
            "asesor",
            "asesores",
            "nombre autores asesores",
        }

    def _advisor_matricula(self, name, email, project, folio):
        source = "|".join([
            self._normalize_excel_header(name),
            self._normalize_excel_header(email),
            self._normalize_excel_header(project),
            self._normalize_excel_header(folio),
        ])
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12].upper()
        return f"ASE-{digest}"

    def _looks_like_event_excel(self, df):
        return (
            self._find_excel_column(df, ["Proyecto"]) is not None
            and self._find_excel_column(df, ["Nombre Autores/Asesores", "Nombre Autores", "Autores", "Asesores"]) is not None
            and self._find_excel_column(df, ["Num. Control/Departamento", "Numero Control Departamento", "Num Control", "Departamento"]) is not None
        )

    def _convert_event_excel(self, df):
        project_col = self._find_excel_column(df, ["Proyecto"])
        folio_col = self._find_excel_column(df, ["Folio"])
        event_col = self._find_excel_column(df, ["Evento"])
        category_col = self._find_excel_column(df, ["Categoria", "Categoría"])
        level_col = self._find_excel_column(df, ["Nivel"])
        semester_col = self._find_excel_column(df, ["Semestre"])
        career_col = self._find_excel_column(df, ["Carrera"])
        name_col = self._find_excel_column(df, ["Nombre Autores/Asesores", "Nombre Autores", "Nombre Asesores", "Autores", "Asesores"])
        email_col = self._find_excel_column(df, ["E-mail Autores/Asesores", "Email Autores/Asesores", "Correo Autores/Asesores", "E-mail", "Email"])
        control_col = self._find_excel_column(df, ["Num. Control/Departamento", "Numero Control Departamento", "Num Control", "No Control", "Departamento"])

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
            project_value = self._cell_text(row[project_col]) if project_col else ""
            folio_value = self._cell_text(row[folio_col]) if folio_col else ""
            is_project_header = bool(project_value and re.match(r"^\d+-\d+$", folio_value))
            if project_value and not is_project_header and not self._is_placeholder_participant_name(project_value):
                current["description"] = project_value

            values = {
                "project": project_value if is_project_header else "",
                "folio": self._cell_text(row[folio_col]) if folio_col else "",
                "event": self._cell_text(row[event_col]) if event_col else "",
                "category": self._cell_text(row[category_col]) if category_col else "",
                "level": self._cell_text(row[level_col]) if level_col else "",
                "semester": self._cell_text(row[semester_col]) if semester_col else "",
                "career": self._cell_text(row[career_col]) if career_col else "",
            }
            for key, value in values.items():
                if value:
                    current[key] = value

            names = self._split_cell_items(row[name_col]) if name_col else []
            emails = self._split_cell_items(row[email_col], split_commas=True) if email_col else []
            controls = self._split_cell_items(row[control_col], split_commas=True) if control_col else []

            for index, name in enumerate(names):
                if self._is_placeholder_participant_name(name):
                    continue

                email = emails[index] if index < len(emails) else (emails[0] if len(emails) == 1 else "")
                control = controls[index] if index < len(controls) else (controls[0] if len(controls) == 1 else "")
                combined = self._normalize_excel_header(f"{name} {email} {control}")
                is_advisor = "asesor" in combined or (control and any(char.isalpha() for char in control) and not any(char.isdigit() for char in control))
                participant_type = "asesor" if is_advisor else "alumno"
                first_name, last_name_p, last_name_m = self._split_full_name(name)
                matricula = control
                if participant_type == "asesor":
                    matricula = self._advisor_matricula(name, email, current["project"], current["folio"])
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

    def prepare_excel_import_dataframe(self, file):
        df = self._read_excel_table(file)
        required_columns = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        if all(col in df.columns for col in required_columns):
            return df, "standard"
        if self._looks_like_event_excel(df):
            return self._convert_event_excel(df), "event_format"
        return df, "unknown"

    def update_user_role(self, username, role):
        role_value = self.normalize_role(role)
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("UPDATE users SET role = %s WHERE username = %s", (role_value, username))
        conn.commit()
        updated = c.rowcount
        conn.close()
        return updated > 0

    def ensure_user(self, username, password_hash, role='guest'):
        role_value = self.normalize_role(role)
        conn = mysql.connector.connect(**self.db_config)
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

    def add_student(self, student_id, first_name, last_name_p, last_name_m, matricula, carrera, project_id, event_id=None, email=None, participant_type='alumno'):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                  (student_id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id))
        try:
            participant_id = self._ensure_participant_for_student(
                c, student_id, first_name, last_name_p, last_name_m, project_id, event_id, email, participant_type
            )
            self._ensure_credential_for_participant(c, participant_id)
        except Exception:
            pass
        conn.commit()
        conn.close()

    def _new_credential_token(self):
        return f"CRD-{secrets.token_hex(4)}"

    def _ensure_participant_for_student(self, cursor, student_id, first_name, last_name_p, last_name_m, project_id, event_id=None, email=None, participant_type='alumno'):
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

    def _ensure_credential_for_participant(self, cursor, participant_id):
        cursor.execute(
            "SELECT id, token, qr_path FROM credentials WHERE participant_id = %s ORDER BY created_at DESC LIMIT 1",
            (participant_id,)
        )
        existing = cursor.fetchone()
        if existing:
            return {"id": existing[0], "token": existing[1], "qr_path": existing[2]}

        now = datetime.now()
        for _ in range(5):
            token = self._new_credential_token()
            try:
                credential_id_type = self._get_column_data_type(cursor, 'credentials', 'id')
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
                return {"id": credential_id, "token": token, "qr_path": None}
            except mysql.connector.Error as e:
                if e.errno != 1062:
                    raise
        raise ValueError("No se pudo generar un token unico para la credencial")

    def ensure_student_participant_credential(self, student):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        participant_id = self._ensure_participant_for_student(
            c, student[0], student[1], student[2], student[3], student[6], student[7] if len(student) > 7 else None
        )
        credential = self._ensure_credential_for_participant(c, participant_id)
        conn.commit()
        conn.close()
        return credential

    def get_participant_id_by_student_id(self, student_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id FROM participants WHERE legacy_student_id = %s", (student_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None

    def save_participant_field_values(self, participant_id, field_values):
        if not participant_id or not field_values:
            return

        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        self._save_participant_field_values(c, participant_id, field_values)
        conn.commit()
        conn.close()

    def _save_participant_field_values(self, cursor, participant_id, field_values):
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

    def get_field_values_by_student_ids(self, student_ids):
        if not student_ids:
            return {}

        placeholders = ",".join(["%s"] * len(student_ids))
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            f"""SELECT p.legacy_student_id, pf.name, pfv.value
                FROM participant_field_values pfv
                JOIN project_fields pf ON pfv.field_id = pf.id
                JOIN participants p ON pfv.participant_id = p.id
                WHERE p.legacy_student_id IN ({placeholders})
                ORDER BY pf.display_order, pf.id""",
            tuple(student_ids)
        )
        values = {}
        for student_id, name, value in c.fetchall():
            values.setdefault(student_id, []).append({"name": name, "value": value})
        c.execute(
            f"""SELECT p.legacy_student_id, ef.name, pefv.value
                FROM participant_event_field_values pefv
                JOIN event_fields ef ON pefv.field_id = ef.id
                JOIN participants p ON pefv.participant_id = p.id
                WHERE p.legacy_student_id IN ({placeholders})
                ORDER BY ef.display_order, ef.id""",
            tuple(student_ids)
        )
        for student_id, name, value in c.fetchall():
            values.setdefault(student_id, []).append({"name": name, "value": value})
        conn.close()
        return values

    def update_credential_qr_path(self, token, qr_path):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            "UPDATE credentials SET qr_path = %s, updated_at = %s WHERE token = %s",
            (qr_path, datetime.now(), token)
        )
        conn.commit()
        conn.close()

    def update_credentials_sent_status(self, credential_ids, status):
        if not credential_ids:
            return
        placeholders = ",".join(["%s"] * len(credential_ids))
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            f"UPDATE credentials SET sent_status = %s, updated_at = %s WHERE id IN ({placeholders})",
            tuple([status, datetime.now()] + credential_ids)
        )
        conn.commit()
        conn.close()

    def log_email(self, event_id, recipient, subject, status, error=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            """INSERT INTO email_logs (event_id, recipient, subject, status, error, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (event_id, recipient, subject, status, error, datetime.now())
        )
        conn.commit()
        conn.close()

    def get_event_credential_rows(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor(dictionary=True)
        c.execute(
            """SELECT p.id AS participant_id, p.full_name, p.email, p.participant_type,
                      p.project_id, pr.name AS project_name, e.name AS event_name,
                      s.matricula, c.id AS credential_id, c.token, c.qr_path
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

    def get_event_email_logs(self, event_id, limit=25):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_event_attendance_events(self, event_id, limit=25):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_students_for_credentials(self, event_id=None):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_event_counts(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_event_template(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
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

    def save_event_template(self, event_id, email_subject, email_body, credential_style='standard', logo_filename=None):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_user_event_permissions(self, username):
        conn = mysql.connector.connect(**self.db_config)
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

    def set_user_event_permissions(self, username, event_ids):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_credential_by_token(self, token):
        conn = mysql.connector.connect(**self.db_config)
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

    def record_attendance_event(self, participant_id, credential_id, legacy_attendance_id, event_type, timestamp, event_id=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            """INSERT INTO attendance_events
               (participant_id, credential_id, legacy_attendance_id, event_type, timestamp, event_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (participant_id, credential_id, legacy_attendance_id, event_type or 'entrada', timestamp, event_id or None)
        )
        conn.commit()
        conn.close()

    def get_all_projects(self):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            """SELECT p.id, p.name, p.description, e.name
               FROM projects p
               LEFT JOIN events e ON p.event_id = e.id"""
        )
        projects = c.fetchall()
        conn.close()
        return projects

    def get_projects_by_event(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_project(self, project_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            """SELECT p.id, p.name, p.description, e.name
               FROM projects p
               LEFT JOIN events e ON p.event_id = e.id
               WHERE p.id = %s""",
            (project_id,)
        )
        project = c.fetchone()
        conn.close()
        return project

    def add_project(self, name, description=None, event_id=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            "INSERT INTO projects (name, description, event_id) VALUES (%s, %s, %s)",
            (name, description, event_id or None)
        )
        conn.commit()
        project_id = c.lastrowid
        conn.close()
        return project_id

    def _get_or_create_project_by_name_with_cursor(self, cursor, name, event_id):
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

    def get_or_create_project_by_name(self, name, event_id):
        project_name = (name or "").strip()
        if not project_name:
            return None

        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        project_id = self._get_or_create_project_by_name_with_cursor(c, project_name, event_id)
        conn.commit()
        conn.close()
        return project_id

    def get_all_events(self):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_event(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute(
            """SELECT id, name, description, start_datetime, end_datetime, location, status, event_type, duplicate_policy
               FROM events WHERE id = %s""",
            (event_id,)
        )
        event = c.fetchone()
        conn.close()
        return event

    def update_event(self, event_id, name, description=None, start_datetime=None, end_datetime=None,
                     location=None, status='active', event_type='general', duplicate_policy='once_per_day'):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_latest_event(self):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_active_events(self):
        conn = mysql.connector.connect(**self.db_config)
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

    def add_event(self, name, description=None, start_datetime=None, end_datetime=None,
                  location=None, status='active', event_type='general', duplicate_policy='once_per_day'):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_project_fields(self, project_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        type_column = 'field_type'
        if not self._column_exists(c, 'project_fields', 'field_type') and self._column_exists(c, 'project_fields', 'type'):
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

    def get_project_fields_as_dicts(self, project_id):
        return [
            {
                "id": field[0],
                "project_id": field[1],
                "name": field[2],
                "field_type": field[3],
                "is_required": bool(field[4]),
                "display_order": field[5],
            }
            for field in self.get_project_fields(project_id)
        ]

    def get_event_fields(self, event_id):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_event_fields_as_dicts(self, event_id):
        return [
            {
                "id": field[0],
                "event_id": field[1],
                "name": field[2],
                "field_type": field[3],
                "is_required": bool(field[4]),
                "display_order": field[5],
            }
            for field in self.get_event_fields(event_id)
        ]

    def add_event_field(self, event_id, name, field_type='text', is_required=False, display_order=0):
        conn = mysql.connector.connect(**self.db_config)
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

    def delete_event_field(self, field_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("DELETE FROM participant_event_field_values WHERE field_id = %s", (field_id,))
        c.execute("DELETE FROM event_fields WHERE id = %s", (field_id,))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0

    def update_event_rules(self, event_id, duplicate_policy, status=None):
        conn = mysql.connector.connect(**self.db_config)
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

    def save_participant_event_field_values(self, participant_id, field_values):
        if not participant_id or not field_values:
            return

        conn = mysql.connector.connect(**self.db_config)
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

    def add_project_field(self, project_id, name, field_type='text', is_required=False, display_order=0):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
        if not c.fetchone():
            conn.close()
            raise ValueError("Proyecto no encontrado")

        type_column = 'field_type'
        if not self._column_exists(c, 'project_fields', 'field_type') and self._column_exists(c, 'project_fields', 'type'):
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

    def delete_project_field(self, field_id):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("DELETE FROM project_fields WHERE id = %s", (field_id,))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0

    def get_total_students(self, event_id=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        if event_id:
            c.execute("SELECT COUNT(*) FROM students WHERE event_id = %s", (event_id,))
        else:
            c.execute("SELECT COUNT(*) FROM students")
        total = c.fetchone()[0]
        conn.close()
        return total

    def get_total_attendance(self, event_id=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        if event_id:
            c.execute("SELECT COUNT(*) FROM attendance_events WHERE event_id = %s", (event_id,))
        else:
            c.execute("SELECT COUNT(*) FROM attendance")
        total = c.fetchone()[0]
        conn.close()
        return total

    def get_all_students(self):
        conn = mysql.connector.connect(**self.db_config)
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

    def get_student_by_matricula(self, matricula):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        c.execute("SELECT id, first_name, last_name_p, last_name_m, matricula, carrera, project_id, event_id FROM students WHERE matricula = %s", (matricula,))
        student = c.fetchone()
        conn.close()
        return student

    def get_student_by_id(self, student_id):
        conn = mysql.connector.connect(**self.db_config)
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

    def update_student_participant(self, student_id, first_name, last_name_p, last_name_m, matricula,
                                   carrera, project_id=None, email=None, participant_type='alumno'):
        conn = mysql.connector.connect(**self.db_config)
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

    def upload_students_from_excel(self, file, project_id, event_id=None):
        df, _ = self.prepare_excel_import_dataframe(file)
        required_columns = ['first_name', 'last_name_p', 'last_name_m', 'matricula', 'carrera']
        if not all(col in df.columns for col in required_columns):
            raise ValueError(
                "El Excel debe contener las columnas: first_name, last_name_p, last_name_m, matricula, carrera "
                "o el formato con Proyecto, Folio, Evento, Nombre Autores/Asesores y Num. Control/Departamento"
            )

        conn = mysql.connector.connect(**self.db_config)
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
            event_fields = self.get_event_fields(event_id)

        if project_id:
            c.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not c.fetchone():
                conn.close()
                raise ValueError("Proyecto no encontrado")
            project_fields = self.get_project_fields(project_id)
            project_fields_cache[int(project_id)] = project_fields

        if project_column and project_column != 'project_id':
            project_names = []
            for raw_project in df[project_column].dropna().tolist():
                project_name = str(raw_project).strip()
                if project_name and project_name not in project_lookup:
                    project_names.append(project_name)
                    project_lookup[project_name] = None
            for project_name in project_names:
                project_lookup[project_name] = self._get_or_create_project_by_name_with_cursor(c, project_name, event_id)
            conn.commit()

        required_dynamic_columns = [field[2] for field in project_fields if field[4]]
        required_dynamic_columns += [
            field[2]
            for field in event_fields
            if field[4] and self._normalize_field_name(field[2]) not in self.BASE_REGISTRATION_FIELD_NAMES
        ]
        missing_dynamic_columns = [
            name
            for name in required_dynamic_columns
            if self._find_excel_column(df, [name]) is None
            and self._normalize_field_name(name) not in ("correo", "email")
        ]
        if missing_dynamic_columns:
            conn.close()
            raise ValueError(
                "El Excel debe contener las columnas configuradas como obligatorias: "
                + ", ".join(missing_dynamic_columns)
            )

        for index, row in df.iterrows():
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
                                row_project_id = self._get_or_create_project_by_name_with_cursor(c, raw_project, event_id)
                                project_lookup[raw_project] = row_project_id
                if row_project_id:
                    imported_project_ids.add(row_project_id)
                row_project_fields = project_fields
                if row_project_id and row_project_id != project_id:
                    if row_project_id not in project_fields_cache:
                        project_fields_cache[row_project_id] = self.get_project_fields(row_project_id)
                    row_project_fields = project_fields_cache[row_project_id]
                dynamic_values = {}
                for field in row_project_fields:
                    field_id = field[0]
                    field_name = field[2]
                    is_required = bool(field[4])
                    raw_value = self._row_value_for_field(row, field_name)
                    value = "" if pd.isna(raw_value) else str(raw_value).strip()
                    if is_required and not value:
                        raise ValueError(f"Falta {field_name}")
                    if value:
                        dynamic_values[field_id] = value

                event_dynamic_values = {}
                for field in event_fields:
                    field_id = field[0]
                    field_name = field[2]
                    if self._normalize_field_name(field_name) in self.BASE_REGISTRATION_FIELD_NAMES:
                        continue
                    is_required = bool(field[4])
                    raw_value = self._row_value_for_field(row, field_name)
                    value = "" if pd.isna(raw_value) else str(raw_value).strip()
                    if is_required and not value:
                        raise ValueError(f"Falta {field_name}")
                    if value:
                        event_dynamic_values[field_id] = value
                    if self._normalize_field_name(field_name) in ('correo', 'email') and value:
                        email_value = value

                c.execute('''INSERT INTO students (id, first_name, last_name_p, last_name_m, matricula, carrera, event_id, project_id)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                          (student_id, str(row['first_name']), str(row['last_name_p']), str(row['last_name_m']),
                           matricula, str(row['carrera']), event_id, row_project_id))
                participant_id = self._ensure_participant_for_student(
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
                self._ensure_credential_for_participant(c, participant_id)
                self._save_participant_field_values(c, participant_id, dynamic_values)
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
        project_summary = ""
        if project_column and imported_project_ids:
            project_summary = f" Proyectos asociados/creados: {len(imported_project_ids)}."
        if errors:
            return f"Procesados {success_count} estudiantes.{project_summary} Errores: {', '.join(errors)}"
        return f"Se procesaron {success_count} estudiantes correctamente.{project_summary}"

    def export_attendance_to_excel(self, project_id, start_date, end_date):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name, a.timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(a.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(a.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'attendance_report_{timestamp}.xlsx').replace('\\', '/')

        workbook = xlsxwriter.Workbook(report_path)
        worksheet = workbook.add_worksheet()
        worksheet.write('A1', 'Reporte de Asistencias - AsisTec')
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

        return report_path

    def get_event_attendance_report(self, event_id, start_date=None, end_date=None, project_id=None):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera,
                   p.name, e.name, ae.event_type, ae.timestamp
            FROM attendance_events ae
            LEFT JOIN credentials cdr ON ae.credential_id = cdr.id
            LEFT JOIN participants part ON ae.participant_id = part.id
            LEFT JOIN students s ON part.legacy_student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN events e ON ae.event_id = e.id
            WHERE ae.event_id = %s
        """
        params = [event_id]
        if start_date:
            query += " AND DATE(ae.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(ae.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        query += " ORDER BY ae.timestamp DESC"
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()
        return report_data

    def export_event_attendance_to_excel(self, event_id, project_id, start_date, end_date):
        report_data = self.get_event_attendance_report(event_id, start_date, end_date, project_id)
        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'event_report_{timestamp}.xlsx').replace('\\', '/')

        workbook = xlsxwriter.Workbook(report_path)
        worksheet = workbook.add_worksheet()
        worksheet.write('A1', 'Reporte de Asistencias por Evento')
        headers = ['Matricula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Evento', 'Tipo', 'Fecha/Hora']
        for col, header in enumerate(headers):
            worksheet.write(1, col, header)
        for row_idx, row in enumerate(report_data, 2):
            for col_idx, value in enumerate(row):
                worksheet.write(row_idx, col_idx, value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else value)
        workbook.close()
        return report_path

    def export_event_attendance_to_pdf(self, event_id, project_id, start_date, end_date):
        report_data = self.get_event_attendance_report(event_id, start_date, end_date, project_id)
        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'event_report_{timestamp}.pdf').replace('\\', '/')

        doc = SimpleDocTemplate(report_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Reporte de Asistencias por Evento", styles['Title']))

        data = [['Matricula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Evento', 'Tipo', 'Fecha/Hora']]
        for row in report_data:
            data.append([
                row[0], row[1], row[2], row[3], row[4], row[5] or 'Sin proyecto',
                row[6] or 'Sin evento', row[7], row[8].strftime('%Y-%m-%d %H:%M:%S') if hasattr(row[8], 'strftime') else row[8]
            ])

        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        doc.build(elements)
        return report_path

    def export_attendance_to_pdf(self, project_id, start_date, end_date):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name, a.timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(a.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(a.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'attendance_report_{timestamp}.pdf').replace('\\', '/')

        doc = SimpleDocTemplate(report_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Reporte de Asistencias - AsisTec", styles['Title']))

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

        return report_path

    def _student_filter_where(self, matricula_search='', apellido_p_search='', project_id_filter='', event_id_filter=''):
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

    def count_students_filtered(self, matricula_search='', apellido_p_search='', project_id_filter='', event_id_filter=''):
        conn = mysql.connector.connect(**self.db_config)
        c = conn.cursor()
        where_clause, params = self._student_filter_where(
            matricula_search,
            apellido_p_search,
            project_id_filter,
            event_id_filter,
        )
        c.execute(f"SELECT COUNT(*) FROM students s WHERE {where_clause}", params)
        total = c.fetchone()[0]
        conn.close()
        return total

    def get_all_students_filtered(
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
        conn = mysql.connector.connect(**self.db_config)
        try:
            query, params = self._filtered_students_query(
                matricula_search, apellido_p_search, project_id_filter, event_id_filter, sort_by, sort_dir, limit, offset
            )
            c = conn.cursor()
            c.execute(query, params)
            return c.fetchall()
        finally:
            conn.close()

    def _filtered_students_query(self, matricula, apellido, project_id, event_id, sort_by, sort_dir, limit, offset):
        where_clause, params = self._student_filter_where(matricula, apellido, project_id, event_id)
        query = self._filtered_students_select(where_clause, sort_by, sort_dir)
        if limit is not None:
            query += " LIMIT %s OFFSET %s"
            params.extend([int(limit), int(offset or 0)])
        return query, params

    def _filtered_students_select(self, where_clause, sort_by, sort_dir):
        sort_expression = self._student_sort_expression(sort_by)
        direction = 'DESC' if str(sort_dir).lower() == 'desc' else 'ASC'
        return f"""
            SELECT s.id, s.first_name, s.last_name_p, s.last_name_m, s.matricula, s.carrera,
                   s.project_id, s.event_id, COALESCE(p.participant_type, 'alumno'), pr.name AS project_name,
                   (
                       SELECT cr.token
                       FROM credentials cr
                       WHERE cr.participant_id = p.id
                       ORDER BY cr.created_at DESC
                       LIMIT 1
                   ) AS credential_token,
                   (
                       SELECT cr.qr_path
                       FROM credentials cr
                       WHERE cr.participant_id = p.id
                       ORDER BY cr.created_at DESC
                       LIMIT 1
                   ) AS credential_qr_path
            FROM students s
            LEFT JOIN participants p ON p.legacy_student_id = s.id
            LEFT JOIN projects pr ON s.project_id = pr.id
            WHERE {where_clause}
            ORDER BY {sort_expression} {direction}, s.last_name_p ASC, s.first_name ASC, s.matricula ASC
        """

    def _student_sort_expression(self, sort_by):
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
