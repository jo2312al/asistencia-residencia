#!/bin/bash
# Script to automate deployment of the Innovatec Flask app on an Ubuntu VM

set -e

# Configuration
APP_DIR="/home/azureuser/app"
USER="azureuser"
SERVICE_NAME="innovatec"

echo "================================================="
echo " Innovatec Flask App - Ubuntu VM Setup Script"
echo "================================================="

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo ./deploy_ubuntu_vm.sh)"
  exit 1
fi

echo "[1/6] Updating system and installing dependencies..."
apt update
apt install -y python3-pip python3-venv nginx curl

echo "[2/6] Setting up application directory and permissions..."
if [ ! -d "$APP_DIR" ]; then
    echo "Directory $APP_DIR does not exist. Please clone the repository first."
    exit 1
fi
chown -R $USER:$USER $APP_DIR

echo "[3/6] Setting up Python virtual environment..."
sudo -u $USER bash -c "cd $APP_DIR && python3 -m venv venv"
sudo -u $USER bash -c "cd $APP_DIR && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# Environment variables warning
if [ ! -f "$APP_DIR/.env" ]; then
    echo "Creating a template .env file..."
    sudo -u $USER bash -c "cd $APP_DIR && cp .env.example .env"
    echo "WARNING: Please configure $APP_DIR/.env with your database credentials and secret key after this script finishes."
fi

echo "[4/6] Configuring Gunicorn systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Gunicorn instance to serve $SERVICE_NAME app
After=network.target

[Service]
User=$USER
Group=www-data
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 3 --bind unix:app.sock -m 007 app:app

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl start $SERVICE_NAME
systemctl enable $SERVICE_NAME

echo "[5/6] Configuring Nginx reverse proxy..."
# Get public IP or use hostname
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com || echo "localhost")

cat > /etc/nginx/sites-available/$SERVICE_NAME << EOF
server {
    listen 80;
    server_name $PUBLIC_IP;

    location / {
        include proxy_params;
        proxy_pass http://unix:$APP_DIR/app.sock;
    }
}
EOF

# Enable site and disable default
ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    rm /etc/nginx/sites-enabled/default
fi

# Ensure www-data can access the app directory for the socket
usermod -a -G $USER www-data

echo "[6/6] Restarting Nginx..."
systemctl restart nginx

echo "================================================="
echo " Setup complete! "
echo "================================================="
echo "Your app should now be running."
echo "1. Remember to edit your .env file: nano $APP_DIR/.env"
echo "2. After editing .env, restart the service: sudo systemctl restart $SERVICE_NAME"
echo "3. Access your app at: http://$PUBLIC_IP"
echo "================================================="
