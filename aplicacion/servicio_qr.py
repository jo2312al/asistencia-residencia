"""Coordinación de credenciales y generación QR fuera de la capa web."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from qr_manager import GestorQR


class ServicioCredencialesQR:
    def __init__(self, repositorio, gestor_qr=None, trabajadores=2, registrador=None):
        """Realiza internamente la operación init."""
        self.repositorio = repositorio
        self.gestor_qr = gestor_qr or GestorQR()
        self.registrador = registrador
        self._ejecutor = ThreadPoolExecutor(max_workers=trabajadores)
        self._enviados = set()
        self._candado = Lock()

    def asegurar_credencial(self, credencial):
        """Ejecuta la operación asegurar credencial y devuelve el resultado correspondiente."""
        token = credencial["token"]
        ruta = credencial.get("qr_path") or self._ruta_token(token)
        if not os.path.exists(ruta):
            ruta = self.gestor_qr.generar_desde_datos(token, token)
        if credencial.get("qr_path") != ruta:
            self.repositorio.actualizar_credencial_qr_ruta(token, ruta)
        return ruta

    def asegurar_participante(self, participante, gestor_qr=None):
        """Ejecuta la operación asegurar participante y devuelve el resultado correspondiente."""
        gestor = gestor_qr or self.gestor_qr
        matricula = participante[4]
        try:
            credencial = self.repositorio.asegurar_participante_participante_credencial(participante)
            ruta = credencial.get("qr_path") or self._ruta_token(credencial["token"])
            if not os.path.exists(ruta):
                ruta = gestor.generar_desde_datos(credencial["token"], credencial["token"])
            if credencial.get("qr_path") != ruta:
                self.repositorio.actualizar_credencial_qr_ruta(credencial["token"], ruta)
            return ruta, credencial["token"], False
        except Exception as error:
            if self.registrador:
                self.registrador.exception(
                    "Falling back to legacy QR for %s: %s", matricula, error
                )
            ruta = self._ruta_token(matricula)
            if not os.path.exists(ruta):
                ruta = gestor.generar(matricula)
            return ruta, None, True

    def asegurar_fila(self, fila, gestor_qr=None):
        """Ejecuta la operación asegurar fila y devuelve el resultado correspondiente."""
        token = fila.get("credential_token")
        if token:
            ruta = fila.get("qr_path") or self._ruta_token(token)
            if not os.path.exists(ruta):
                ruta = (gestor_qr or self.gestor_qr).generar_desde_datos(token, token)
            if fila.get("qr_path") != ruta:
                self.repositorio.actualizar_credencial_qr_ruta(token, ruta)
            return ruta, token, False
        return self.asegurar_participante(self.fila_a_tupla(fila), gestor_qr)

    @staticmethod
    def fila_a_tupla(fila):
        """Ejecuta la operación fila a tupla y devuelve el resultado correspondiente."""
        return (
            fila["id"], fila["first_name"], fila["last_name_p"],
            fila["last_name_m"], fila["matricula"], fila["carrera"],
            fila.get("project_id"), fila.get("event_id"),
            fila.get("participant_type") or "alumno",
        )

    @staticmethod
    def diccionario_a_tupla(participante):
        """Ejecuta la operación diccionario a tupla y devuelve el resultado correspondiente."""
        return (
            participante["id"], participante["first_name"],
            participante["last_name_p"], participante["last_name_m"],
            participante["matricula"], participante["carrera"],
            participante.get("project_id"), participante.get("event_id"),
            participante.get("participant_type") or "alumno",
            participante.get("project_name"),
        )

    def encolar(self, participantes):
        """Ejecuta la operación encolar y devuelve el resultado correspondiente."""
        for participante in participantes:
            if self.necesita_qr(participante):
                self.enviar_trabajo(tuple(participante[:9]))

    @staticmethod
    def necesita_qr(participante):
        """Ejecuta la operación necesita qr y devuelve el resultado correspondiente."""
        token = participante[10] if len(participante) > 10 else None
        ruta = participante[11] if len(participante) > 11 else None
        return not token or not ruta

    def enviar_trabajo(self, participante):
        """Ejecuta la operación enviar trabajo y devuelve el resultado correspondiente."""
        clave = participante[0]
        if not self.marcar_enviado(clave):
            return
        futuro = self._ejecutor.submit(self._generar_en_segundo_plano, participante)
        futuro.add_done_callback(lambda _: self.desmarcar_enviado(clave))

    def marcar_enviado(self, clave):
        """Ejecuta la operación marcar enviado y devuelve el resultado correspondiente."""
        with self._candado:
            if clave in self._enviados:
                return False
            self._enviados.add(clave)
            return True

    def desmarcar_enviado(self, clave):
        """Ejecuta la operación desmarcar enviado y devuelve el resultado correspondiente."""
        with self._candado:
            self._enviados.discard(clave)

    def _generar_en_segundo_plano(self, participante):
        """Realiza internamente la operación generar en segundo plano."""
        try:
            self.asegurar_participante(participante, GestorQR())
        except Exception:
            if self.registrador:
                self.registrador.exception(
                    "No se pudo generar QR en segundo plano para %s", participante[0]
                )

    @staticmethod
    def _ruta_token(token):
        """Realiza internamente la operación ruta token."""
        return os.path.join("static/qr_codes", f"{token}.png").replace("\\", "/")

