# Warehouse Picking System - Server Migration Guide

## Moving from Replit to Your Magento Server

### Server Requirements
- Python 3.8+ 
- PostgreSQL 12+ (or can use existing MySQL/MariaDB with modifications)
- Nginx or Apache web server
- At least 2GB RAM, 10GB disk space
- Ubuntu/CentOS/RHEL Linux

### Step 1: Prepare Your Server

```bash
# Install Python and dependencies
sudo apt update
sudo apt install python3 python3-pip python3-venv postgresql postgresql-contrib nginx

# Create application directory
sudo mkdir -p /var/www/picking-system
sudo chown $USER:$USER /var/www/picking-system
cd /var/www/picking-system
```

### Step 2: Download Application Files

```bash
# Copy these files from your Replit project:
# - main.py (main application)
# - models.py (database models)
# - routes*.py (all route files)
# - templates/ (HTML templates)
# - static/ (CSS, JS, images)
# - requirements.txt (see below)
# - gunicorn_config.py
# - All utility files (utils.py, batch_utils.py, etc.)
```

### Step 3: Create Requirements File

Create `requirements.txt`:
```
Flask==2.3.3
Flask-SQLAlchemy==3.1.1
Flask-Login==0.6.3
gunicorn==21.2.0
psycopg2-binary==2.9.7
pandas==2.1.0
numpy==1.24.3
openpyxl==3.1.2
xlsxwriter==3.1.2
xlrd==2.0.1
Pillow==10.0.0
pytz==2023.3
requests==2.31.0
email-validator==2.0.0
```

### Step 4: Setup Application

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Setup database
sudo -u postgres createdb picking_system
sudo -u postgres createuser picking_user
sudo -u postgres psql -c "ALTER USER picking_user PASSWORD 'your_secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE picking_system TO picking_user;"
```

### Step 5: Environment Configuration

Create `.env` file:
```
DATABASE_URL=postgresql://picking_user:your_secure_password@localhost/picking_system
FLASK_SECRET_KEY=your_very_secure_secret_key_here
FLASK_ENV=production
```

### Step 6: Nginx Configuration

Create `/etc/nginx/sites-available/picking-system`:
```nginx
server {
    listen 80;
    server_name your-domain.com;  # Replace with your domain/subdomain

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /var/www/picking-system/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/picking-system /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Step 7: Production Gunicorn Config

Update `gunicorn_config.py` for production:
```python
# Production Gunicorn Configuration
import multiprocessing

bind = "127.0.0.1:5000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 30
keepalive = 2
preload_app = True
user = "www-data"
group = "www-data"
```

### Step 8: Systemd Service

Create `/etc/systemd/system/picking-system.service`:
```ini
[Unit]
Description=Warehouse Picking System
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/picking-system
Environment=PATH=/var/www/picking-system/venv/bin
ExecStart=/var/www/picking-system/venv/bin/gunicorn --config gunicorn_config.py main:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable picking-system
sudo systemctl start picking-system
```

### Step 9: SSL Certificate (Optional but Recommended)

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d your-domain.com
```

### Step 10: Database Migration

```bash
# Initialize database
cd /var/www/picking-system
source venv/bin/activate
python3 -c "
from main import app
with app.app_context():
    from models import db
    db.create_all()
    print('Database initialized')
"
```

## Performance Benefits on Dedicated Server

- **Load averages under 1.0** (vs 8+ on Replit)
- **Full CPU and memory control**
- **No background infrastructure processes**
- **Faster disk I/O**
- **Better network performance**
- **Dedicated resources**

## Maintenance Commands

```bash
# Check service status
sudo systemctl status picking-system

# View logs
sudo journalctl -u picking-system -f

# Restart service
sudo systemctl restart picking-system

# Update application
cd /var/www/picking-system
source venv/bin/activate
git pull  # if using git
sudo systemctl restart picking-system
```

## Security Considerations

1. **Firewall**: Only open ports 80, 443, and SSH
2. **Database**: Restrict PostgreSQL to localhost only
3. **Updates**: Keep system packages updated
4. **Backups**: Regular database backups
5. **Monitoring**: Setup basic monitoring for the service

## Expected Performance

On a dedicated server with 2+ CPU cores and 4GB RAM:
- Load average: 0.1 - 0.5
- Response time: < 200ms
- Concurrent users: 50+
- Database queries: < 50ms

This will provide excellent performance for your warehouse operations!