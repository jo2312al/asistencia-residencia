# Deploy a Azure App Service

## 1) Prerrequisitos
- Azure CLI instalado (`az --version`)
- Cuenta de Azure con permisos para crear recursos
- Base de datos MySQL (puede ser Azure Database for MySQL Flexible Server)

## 2) Crear recursos (Linux App Service)
```bash
az login
az group create --name rg-innovatec --location eastus
az appservice plan create --name plan-innovatec --resource-group rg-innovatec --sku B1 --is-linux
az webapp create --resource-group rg-innovatec --plan plan-innovatec --name <tu-app-name-unico> --runtime "PYTHON:3.12"
```

## 3) Configurar startup command
En Azure Portal, en tu App Service:
- `Configuration` -> `General settings` -> `Startup Command`
- Pegar:
```bash
gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 app:app
```

## 4) Variables de entorno
En Azure Portal, en tu App Service:
- `Configuration` -> `Application settings`

Agregar:
- `SECRET_KEY`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_SSL_DISABLED` (`false` recomendado)
- `DB_SSL_CA` (opcional; si no usas ruta local, deja vacio)
- `ADMIN_USERNAME` (opcional)
- `ADMIN_PASSWORD` (opcional)

## 5) Publicar codigo
Puedes usar zip deploy o GitHub Actions.

### Opcion A: ZIP Deploy
```bash
az webapp deployment source config-zip \
  --resource-group rg-innovatec \
  --name <tu-app-name-unico> \
  --src <ruta-al-zip-del-proyecto>
```

### Opcion B: GitHub Actions
- En App Service -> `Deployment Center`
- Conectar repo y branch
- Azure genera workflow automaticamente

## 6) Verificar
- Abrir `https://<tu-app-name-unico>.azurewebsites.net`
- Revisar login
- Probar dashboards por rol (`admin`, `staff`, `guest`)
- Probar scanner y reportes

## 7) Nota importante de seguridad
- No uses credenciales hardcodeadas en codigo.
- Usa siempre `Application settings` de Azure para secretos.
