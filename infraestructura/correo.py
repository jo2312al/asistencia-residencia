"""Adaptador SMTP independiente de Flask y de los casos de uso."""
import smtplib
from email.message import EmailMessage


class ServicioCorreo:
    def __init__(self, configuracion):
        """Realiza internamente la operación init."""
        self.configuracion = configuracion

    def enviar(self, destinatario, asunto, cuerpo, adjuntos=None):
        """Ejecuta la operación enviar y devuelve el resultado correspondiente."""
        faltantes = [
            clave
            for clave in ("server", "username", "password", "sender")
            if not self.configuracion.get(clave)
        ]
        if faltantes:
            raise RuntimeError(
                "Configura SMTP en Azure: MAIL_SERVER, MAIL_PORT, "
                "MAIL_USERNAME, MAIL_PASSWORD y MAIL_DEFAULT_SENDER"
            )

        mensaje = EmailMessage()
        mensaje["From"] = self.configuracion["sender"]
        mensaje["To"] = destinatario
        mensaje["Subject"] = asunto
        mensaje.set_content(cuerpo)
        for ruta, nombre in adjuntos or []:
            with open(ruta, "rb") as archivo:
                mensaje.add_attachment(
                    archivo.read(), maintype="image", subtype="png", filename=nombre
                )

        clase_smtp = smtplib.SMTP_SSL if self.configuracion["use_ssl"] else smtplib.SMTP
        with clase_smtp(
            self.configuracion["server"], self.configuracion["port"], timeout=30
        ) as servidor:
            if self.configuracion["use_tls"] and not self.configuracion["use_ssl"]:
                servidor.starttls()
            servidor.iniciar_sesion(
                self.configuracion["username"], self.configuracion["password"]
            )
            servidor.send_message(mensaje)

