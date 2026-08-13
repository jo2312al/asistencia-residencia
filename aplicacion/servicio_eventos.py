"""Casos de uso de eventos independientes de Flask."""


CAMPOS_PREDEFINIDOS = {
    "email": ("Correo", "email", True),
    "telefono": ("Telefono", "tel", False),
    "equipo": ("Equipo", "text", False),
    "categoria": ("Categoria", "text", False),
    "institucion": ("Institucion", "text", False),
    "rfc": ("RFC", "text", False),
}


class ServicioEventos:
    def __init__(self, repositorio, normalizar_fecha):
        """Realiza internamente la operación init."""
        self.repositorio = repositorio
        self.normalizar_fecha = normalizar_fecha

    def datos_desde_formulario(self, formulario):
        """Ejecuta la operación datos desde formulario y devuelve el resultado correspondiente."""
        return {
            "name": (formulario.get("name") or "").strip(),
            "description": (formulario.get("description") or "").strip() or None,
            "start_datetime": self.normalizar_fecha(formulario.get("start_datetime")),
            "end_datetime": self.normalizar_fecha(formulario.get("end_datetime")),
            "location": (formulario.get("location") or "").strip() or None,
            "status": formulario.get("status") or "active",
            "event_type": formulario.get("event_type") or "general",
            "duplicate_policy": formulario.get("duplicate_policy") or "once_per_day",
        }

    def crear(self, datos, campos_seleccionados, campo_personalizado):
        """Ejecuta la operación crear y devuelve el resultado correspondiente."""
        event_id = self.repositorio.agregar_evento(*datos.values())
        for orden, clave in enumerate(campos_seleccionados, start=1):
            opcion = CAMPOS_PREDEFINIDOS.get(clave)
            if opcion:
                self.repositorio.agregar_evento_campo(event_id, *opcion, orden)
        personalizado = (campo_personalizado or "").strip()
        if personalizado:
            self.repositorio.agregar_evento_campo(
                event_id, personalizado, "text", False, len(campos_seleccionados) + 1
            )
        return event_id

    def contexto_detalle(self, event_id):
        """Ejecuta la operación contexto detalle y devuelve el resultado correspondiente."""
        evento = self.repositorio.obtener_evento(event_id)
        if not evento:
            return None
        proyectos = self.repositorio.obtener_proyectos_por_evento(event_id)
        participantes = self._filas_participantes(event_id, proyectos)
        return {
            "event": evento,
            "projects": proyectos,
            "participants": participantes,
            "counts": self._seguro(
                lambda: self.repositorio.obtener_evento_conteos(event_id),
                {"participants": len(participantes), "projects": len(proyectos),
                 "attendance": 0, "credentials": 0},
            ),
            "email_logs": self._seguro(
                lambda: self.repositorio.obtener_evento_correo_registros(event_id), []
            ),
            "attendance_events": self._seguro(
                lambda: self.repositorio.obtener_evento_asistencia_eventos(event_id), []
            ),
        }

    def _filas_participantes(self, event_id, proyectos):
        """Realiza internamente la operación filas participantes."""
        participantes = self.repositorio.obtener_todos_participantes_filtrados(event_id_filter=event_id)
        personalizados = self.repositorio.obtener_campo_valores_por_participante_ids(
            [participante[0] for participante in participantes]
        )
        nombres_proyecto = {proyecto[0]: proyecto[1] for proyecto in proyectos}
        return [
            self._fila_participante(p, personalizados, nombres_proyecto)
            for p in participantes
        ]

    def _fila_participante(self, participante, personalizados, nombres_proyecto):
        """Realiza internamente la operación fila participante."""
        detalle = self.repositorio.obtener_participante_por_id(participante[0]) or {}
        return {
            "id": participante[0], "first_name": participante[1],
            "last_name_p": participante[2], "last_name_m": participante[3],
            "matricula": participante[4], "carrera": participante[5],
            "project_id": participante[6], "event_id": participante[7],
            "participant_type": participante[8],
            "project_name": nombres_proyecto.get(participante[6], "Sin proyecto"),
            "email": detalle.get("email") or "",
            "custom_fields": personalizados.get(participante[0], []),
        }

    @staticmethod
    def _seguro(operacion, predeterminado):
        """Realiza internamente la operación seguro."""
        try:
            return operacion()
        except Exception:
            return predeterminado
