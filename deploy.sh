#!/bin/bash
# ══════════════════════════════════════════════════════
#  DocLit — деплой на Debian 12
#  Запускать: bash deploy.sh
#  Из папки:  /var/www/html/doclit.vlasovs.tk
# ══════════════════════════════════════════════════════

set -e

DOMAIN="doclit.vlasovs.tk"
ROOT="/var/www/html/${DOMAIN}"
API="${ROOT}/api_backend"

cd "${ROOT}"

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║       DocLit Deploy — Debian 13            ║"
echo "╠════════════════════════════════════════════╣"
echo "║  Корень: ${ROOT}"
echo "║  API:    ${API}"
echo "╚════════════════════════════════════════════╝"
echo ""


# ─────────────────────────────────────────
# 1. Системные пакеты
# ─────────────────────────────────────────
echo "▶ [1/6] Системные пакеты..."
sudo su
whoami
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    libreoffice-writer libreoffice-calc \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz \
    libgl1 libglib2.0-0 libjpeg-dev zlib1g-dev \
    make build-essential
echo "  ✓ пакеты установлены"


# ─────────────────────────────────────────
# 2. Раскладываем файлы
# ─────────────────────────────────────────
echo ""
echo "▶ [2/6] Раскладываем файлы..."

mkdir -p "${API}/app/api"
mkdir -p "${API}/app/workers"
mkdir -p "${API}/uploads"
mkdir -p "${API}/outputs"

# requirements.txt — лежит рядом со скриптом
cp "${ROOT}/requirements.txt"             "${API}/requirements.txt"

# Python модули из папки app/
cp "${ROOT}/app/main.py"                  "${API}/app/main.py"
cp "${ROOT}/app/database.py"              "${API}/app/database.py"
touch "${API}/app/__init__.py"

cp "${ROOT}/app/api/auth.py"              "${API}/app/api/auth.py"
cp "${ROOT}/app/api/files.py"             "${API}/app/api/files.py"
cp "${ROOT}/app/api/jobs.py"              "${API}/app/api/jobs.py"
touch "${API}/app/api/__init__.py"

cp "${ROOT}/app/workers/processor.py"     "${API}/app/workers/processor.py"
touch "${API}/app/workers/__init__.py"

echo "  ✓ файлы скопированы в ${API}"
find "${API}/app" -name "*.py" | sort


# ─────────────────────────────────────────
# 3. Python venv
# ─────────────────────────────────────────
echo ""
echo "▶ [3/6] Python venv + зависимости..."

cd "${API}"
python3 -m venv venv
source venv/bin/activate
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
python3 -c "import fastapi, fitz, pytesseract, aiosqlite; print('  ✓ все импорты OK')"
deactivate
cd "${ROOT}"


# ─────────────────────────────────────────
# 4. Systemd сервис
# ─────────────────────────────────────────
echo ""
echo "▶ [4/6] Systemd сервис..."

JWT_SECRET=$(openssl rand -hex 32)

cat > /etc/systemd/system/doclit-api.service << UNIT
[Unit]
Description=DocLit FastAPI — ${DOMAIN}
After=network.target

[Service]
User=www-data
WorkingDirectory=${API}
Environment="JWT_SECRET=${JWT_SECRET}"
Environment="DB_PATH=${API}/doclit.db"
ExecStart=${API}/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

chown -R www-data:www-data "${ROOT}"
chown -R www-data:www-data "${API}"

systemctl daemon-reload
systemctl enable doclit-api
systemctl restart doclit-api

sleep 3
if systemctl is-active --quiet doclit-api; then
    echo "  ✓ API запущен"
    curl -s http://127.0.0.1:8000/api/health && echo ""
else
    echo "  ✗ API не запустился. Логи:"
    journalctl -u doclit-api -n 30 --no-pager
    exit 1
fi


# ─────────────────────────────────────────
# 5. Nginx
# ─────────────────────────────────────────
echo ""
echo "▶ [5/6] Nginx..."

NGINX_CONF=""
for f in \
    "/etc/nginx/sites-available/${DOMAIN}" \
    "/etc/nginx/sites-enabled/${DOMAIN}" \
    "/etc/nginx/conf.d/${DOMAIN}.conf"; do
    if [ -f "$f" ]; then
        NGINX_CONF="$f"
        echo "  Найден: $f"
        break
    fi
done

if [ -z "$NGINX_CONF" ]; then
    echo "  ✗ Nginx конфиг не найден! Выполни:"
    echo "  ls /etc/nginx/sites-available/"
    echo "  ls /etc/nginx/conf.d/"
    exit 1
fi

cp "${NGINX_CONF}" "${NGINX_CONF}.bak.$(date +%s)"
echo "  Бэкап сохранён"

SSL_CERT=$(grep -m1 'ssl_certificate '     "${NGINX_CONF}" | awk '{print $2}' | tr -d ';"')
SSL_KEY=$( grep -m1 'ssl_certificate_key'  "${NGINX_CONF}" | awk '{print $2}' | tr -d ';"')
SSL_OPTS=$(grep -m1 'include.*options-ssl' "${NGINX_CONF}" | awk '{print $2}' | tr -d ';"')
SSL_DH=$(  grep -m1 'ssl_dhparam'          "${NGINX_CONF}" | awk '{print $2}' | tr -d ';"')

echo "  SSL cert: ${SSL_CERT}"
echo "  SSL key:  ${SSL_KEY}"

if [ -z "$SSL_CERT" ]; then
    echo "  ✗ SSL не найден в конфиге. Содержимое:"
    cat "${NGINX_CONF}"
    exit 1
fi

cat > "${NGINX_CONF}" << NGINX
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name ${DOMAIN} www.${DOMAIN};

    ssl_certificate     ${SSL_CERT};
    ssl_certificate_key ${SSL_KEY};
    include             ${SSL_OPTS};
    ssl_dhparam         ${SSL_DH};

    root ${ROOT};
    index index.html;

    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        client_max_body_size 55m;
    }

    location /outputs/ {
        proxy_pass       http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        expires 24h;
    }

    location = /app {
        try_files /app.html =404;
    }

    location / {
        try_files \$uri \$uri/ =404;
    }

    gzip on;
    gzip_types text/html text/css application/javascript application/json;
    gzip_min_length 1024;

    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
}
NGINX

nginx -t && systemctl reload nginx && echo "  ✓ Nginx перезагружен"


# ─────────────────────────────────────────
# 6. Cron
# ─────────────────────────────────────────
echo ""
echo "▶ [6/6] Cron..."
(crontab -l 2>/dev/null | grep -v doclit; \
 echo "0 3 * * * find ${API}/uploads -mtime +1 -delete 2>/dev/null; find ${API}/outputs -mtime +1 -delete 2>/dev/null  # doclit") \
 | crontab -
echo "  ✓ автоочистка в 3:00"


echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓  ДЕПЛОЙ ЗАВЕРШЁН                                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  https://${DOMAIN}            ← лендинг     ║"
echo "║  https://${DOMAIN}/app        ← приложение  ║"
echo "║  https://${DOMAIN}/api/health ← health      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  journalctl -u doclit-api -f   ← логи API                   ║"
echo "║  systemctl restart doclit-api  ← рестарт                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
