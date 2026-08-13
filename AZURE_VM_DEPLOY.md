# Deploy on Azure Virtual Machine (Ubuntu)

This guide provides step-by-step instructions to deploy this Flask application on an Azure Virtual Machine running Ubuntu Linux.

## 1. Prerequisites

- Azure CLI installed locally (`az --version`).
- An Azure account with permissions to create resources.
- A MySQL database (e.g., Azure Database for MySQL Flexible Server).
- SSH keys generated locally (if not, run `ssh-keygen -t rsa -b 4096`).

## 2. Create the Azure VM

Using the Azure CLI, create a resource group and the Ubuntu VM.

```bash
az login

# Create a resource group
az group create --name rg-innovatec-vm --location eastus

# Create the VM (Standard_B1s is a low-cost option, adjust as needed)
az vm create \
  --resource-group rg-innovatec-vm \
  --name vm-innovatec \
  --image Ubuntu2204 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --size Standard_B1s

# Open port 80 (HTTP) and 22 (SSH) in the Network Security Group
az vm open-port --port 80 --resource-group rg-innovatec-vm --name vm-innovatec
```

Get the public IP of your new VM:
```bash
az vm show -d -g rg-innovatec-vm -n vm-innovatec --query publicIps -o tsv
```

## 3. SSH into the VM and Deploy

Connect to the VM:
```bash
ssh azureuser@<public-ip-of-vm>
```

### 3.1 Clone the Repository
Once inside the VM, clone your project repository:
```bash
git clone <your-repo-url> app
cd app
```

### 3.2 Automated Setup (Recommended)
You can use the provided setup script to automatically install dependencies, set up a virtual environment, configure Gunicorn as a systemd service, and configure Nginx.

Make the script executable and run it:
```bash
chmod +x deploy_ubuntu_vm.sh
sudo ./deploy_ubuntu_vm.sh
```

### 3.3 Manual Setup (Alternative)
If you prefer to set things up manually, follow these steps:

**Install Dependencies:**
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv nginx
```

**Set up Virtual Environment:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Configure Environment Variables:**
Create a `.env` file in the project root:
```bash
cp .env.example .env
nano .env
```
Ensure you fill in your database credentials and secret key.

**Test Gunicorn:**
```bash
gunicorn --bind 0.0.0.0:8000 app:app
```
(Press Ctrl+C to stop).

**Setup Systemd Service:**
Create `/etc/systemd/system/innovatec.service`:
```ini
[Unit]
Description=Gunicorn instance to serve innovatec app
After=network.target

[Service]
User=azureuser
Group=www-data
WorkingDirectory=/home/azureuser/app
Environment="PATH=/home/azureuser/app/venv/bin"
ExecStart=/home/azureuser/app/venv/bin/gunicorn --workers 3 --bind unix:app.sock -m 007 app:app

[Install]
WantedBy=multi-user.target
```
Start and enable the service:
```bash
sudo systemctl start innovatec
sudo systemctl enable innovatec
```

**Setup Nginx:**
Create `/etc/nginx/sites-available/innovatec`:
```nginx
server {
    listen 80;
    server_name <public-ip-of-vm>;

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/azureuser/app/app.sock;
    }
}
```
Enable the site and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/innovatec /etc/nginx/sites-enabled
sudo rm /etc/nginx/sites-enabled/default
sudo systemctl restart nginx
```

## 4. Verify

Open your browser and navigate to `http://<public-ip-of-vm>`. You should see the application running.
