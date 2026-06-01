# Asistencia

Aplicacion web para gestionar eventos, participantes y registros de asistencia mediante codigos QR.

## Caracteristicas

- Inicio de sesion con roles de administrador, staff y consulta.
- Gestion de eventos, proyectos y participantes.
- Registro de asistencia por QR o matricula.
- Generacion de credenciales y codigos QR.
- Exportacion de reportes en Excel y PDF.
- Despliegue automatizado a Azure Web App mediante GitHub Actions.

## Requisitos

- Python 3.12
- MySQL
- Dependencias de `requirements.txt`

## Configuracion

Copia `.env.example` a `.env` y ajusta las variables de entorno:

```env
SECRET_KEY=change-this-secret
DB_USER=your_mysql_user
DB_PASSWORD=your_mysql_password
DB_HOST=your_mysql_host
DB_PORT=3306
DB_NAME=innovatec
```

## Ejecucion local

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

La aplicacion inicia por defecto en `http://localhost:5000`.

## Despliegue

El workflow de GitHub Actions genera un paquete de despliegue y lo publica en Azure Web App usando el secreto de publish profile configurado en el repositorio.
