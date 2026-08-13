"""Caso de uso para registrar participantes y generar su credencial."""
import uuid


class ServicioRegistroParticipantes:
    def __init__(self, repositorio, normalizar_nombre, campos_base):
        """Realiza internamente la operación init."""
        self.repositorio = repositorio
        self.normalizar_nombre = normalizar_nombre
        self.campos_base = campos_base

    def leer_datos(self, formulario):
        """Ejecuta la operación leer datos y devuelve el resultado correspondiente."""
        return {
            "student_id": str(uuid.uuid4()),
            "first_name": formulario["first_name"],
            "last_name_p": formulario["last_name_p"],
            "last_name_m": formulario["last_name_m"],
            "matricula": formulario["matricula"],
            "carrera": formulario["carrera"],
            "event_id": formulario.get("event_id") or None,
            "project_id": formulario.get("project_id") or None,
            "participant_type": formulario.get("participant_type") or "alumno",
        }

    def validar_contexto(self, datos):
        """Ejecuta la operación validar contexto y devuelve el resultado correspondiente."""
        if not datos["event_id"]:
            return "Selecciona un evento"
        if not self.repositorio.obtener_evento(datos["event_id"]):
            return "Evento no encontrado"
        if datos["project_id"] and not self._proyecto_permitido(datos):
            return f"Proyecto con ID {datos['project_id']} no existe"
        return None

    def _proyecto_permitido(self, datos):
        """Realiza internamente la operación proyecto permitido."""
        permitidos = {
            str(proyecto[0])
            for proyecto in self.repositorio.obtener_proyectos_por_evento(datos["event_id"])
        }
        return str(datos["project_id"]) in permitidos

    def leer_campos_dinamicos(self, formulario, event_id):
        """Ejecuta la operación leer campos dinamicos y devuelve el resultado correspondiente."""
        valores, correo = {}, None
        for campo in self.repositorio.obtener_evento_campos(event_id):
            field_id, field_name, requerido = campo[0], campo[2], bool(campo[4])
            normalizado = self.normalizar_nombre(field_name)
            if normalizado in self.campos_base:
                continue
            valor = (formulario.get(f"field_{field_id}") or "").strip()
            if requerido and not valor:
                raise ValueError(f"Falta el campo {field_name}")
            if valor:
                valores[field_id] = valor
            if normalizado in ("correo", "email") and valor:
                correo = valor
        return valores, correo

    def guardar(self, datos, valores_dinamicos, correo):
        """Ejecuta la operación guardar y devuelve el resultado correspondiente."""
        self.repositorio.agregar_participante(
            datos["student_id"], datos["first_name"], datos["last_name_p"],
            datos["last_name_m"], datos["matricula"], datos["carrera"],
            datos["project_id"], datos["event_id"], correo,
            datos["participant_type"],
        )
        participante = self.repositorio.obtener_participante_por_matricula(datos["matricula"])
        credencial = self.repositorio.asegurar_participante_participante_credencial(participante)
        participant_id = self.repositorio.obtener_participante_id_por_participante_id(participante[0])
        self.repositorio.guardar_participante_evento_campo_valores(participant_id, valores_dinamicos)
        return credencial
