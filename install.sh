#!/usr/bin/env bash
# =============================================================================
#  Установка «Личного кабинета артиста» на чистый сервер Ubuntu 22.04 / 24.04
#
#  Запуск (на сервере, под root):
#    curl -fsSL https://raw.githubusercontent.com/<логин>/<репозиторий>/main/install.sh | bash
#
#  Скрипт можно запускать повторно: он обновит код до последней версии
#  из GitHub и перезапустит сайт, ничего не удаляя.
# =============================================================================
set -euo pipefail

APP_DIR=/opt/artist-cabinet
DATA_DIR=/var/lib/artist-cabinet
SRC_DIR=$APP_DIR/src
ENV_FILE=$APP_DIR/.env
SERVICE=artist-cabinet

say()  { printf '\n\033[1;35m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }
ask()  { # ask "Вопрос" "по умолчанию" -> в переменную REPLY
  local prompt="$1" def="${2:-}"
  if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def" > /dev/tty; else printf '%s: ' "$prompt" > /dev/tty; fi
  IFS= read -r REPLY < /dev/tty || true
  REPLY="${REPLY:-$def}"
}

[ "$(id -u)" -eq 0 ] || die "Запустите от имени root (или добавьте sudo перед bash)"
[ -r /dev/tty ] || die "Нужен интерактивный терминал"

# ---------------------------------------------------------------------------
# Откуда брать код: адрес репозитория определяется из ссылки, по которой
# скачан этот скрипт, либо задаётся переменной REPO (логин/репозиторий).
# ---------------------------------------------------------------------------
REPO="${REPO:-}"
if [ -z "$REPO" ] && [ -f "$APP_DIR/repo" ]; then REPO=$(cat "$APP_DIR/repo"); fi
if [ -z "$REPO" ]; then
  ask "Ваш репозиторий на GitHub в виде логин/название (например ivan/artist-cabinet)"
  REPO="$REPLY"
fi
REPO="${REPO#https://github.com/}"; REPO="${REPO%.git}"; REPO="${REPO%/}"
[[ "$REPO" == */* ]] || die "Нужно указать в виде логин/название, например ivan/artist-cabinet"

say "Устанавливаю системные пакеты (1–3 минуты)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates cron > /dev/null
if ! command -v caddy > /dev/null; then
  apt-get install -y -qq caddy > /dev/null 2>&1 || {
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg > /dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy > /dev/null
  }
fi
ok "Пакеты установлены"

say "Скачиваю код сайта из github.com/$REPO"
mkdir -p "$APP_DIR" "$DATA_DIR"
echo "$REPO" > "$APP_DIR/repo"
TMP=$(mktemp -d)
git clone --depth 1 -q "https://github.com/$REPO.git" "$TMP/repo" \
  || die "Не получилось скачать github.com/$REPO. Проверьте, что репозиторий публичный и название написано верно."
# код может лежать в корне репозитория или во вложенной папке
ROOT=$(dirname "$(find "$TMP/repo" -maxdepth 3 -name manage.py -not -path '*/.git/*' -print -quit)")
[ -f "$ROOT/manage.py" ] || die "В репозитории не найден manage.py — загрузите на GitHub все файлы из архива"
rm -rf "$SRC_DIR.new" && cp -r "$ROOT" "$SRC_DIR.new"
rm -rf "$SRC_DIR.old"; [ -d "$SRC_DIR" ] && mv "$SRC_DIR" "$SRC_DIR.old"
mv "$SRC_DIR.new" "$SRC_DIR"
rm -rf "$TMP"
ok "Код скачан"

# ---------------------------------------------------------------------------
# Настройки (спрашиваем только при первой установке)
# ---------------------------------------------------------------------------
IP=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
if [ ! -f "$ENV_FILE" ] || [ "${RECONFIGURE:-0}" = "1" ]; then
  say "Пара вопросов (чтобы оставить вариант в скобках, просто нажмите Enter)"
  echo "Если своего домена пока нет — нажмите Enter, сайт будет работать по адресу ${IP//./-}.sslip.io" > /dev/tty
  ask "Домен сайта (например cabinet.mylabel.ru)" "${IP//./-}.sslip.io"; DOMAIN="${REPLY#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%/}"
  ask "Название кабинета (видно в шапке сайта)" "Личный кабинет артиста"; SITE_NAME="$REPLY"
  ask "Контакт для артистов, если что-то не так (телеграм или почта)" ""; SUPPORT="$REPLY"
  SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')
  umask 077
  cat > "$ENV_FILE" <<EOF
DOMAIN=$DOMAIN
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
SITE_NAME="$SITE_NAME"
SUPPORT_CONTACT="$SUPPORT"
DEFAULT_CURRENCY=RUB
TIME_ZONE=Europe/Moscow
DATA_DIR=$DATA_DIR
EOF
  umask 022
fi
set -a; . "$ENV_FILE"; set +a

say "Устанавливаю зависимости сайта"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$SRC_DIR/requirements.txt"
cd "$SRC_DIR"
"$APP_DIR/venv/bin/python" manage.py migrate --noinput -v0
"$APP_DIR/venv/bin/python" manage.py collectstatic --noinput -v0
ok "Сайт собран"

# ---------------------------------------------------------------------------
# Администратор
# ---------------------------------------------------------------------------
HAS_ADMIN=$("$APP_DIR/venv/bin/python" manage.py shell -v0 -c \
  "from cabinet.models import User;print(int(User.objects.filter(is_superuser=True).exists()))")
if [ "$HAS_ADMIN" = "0" ]; then
  say "Создаём ваш аккаунт администратора"
  ask "Ваш email для входа в админку"; ADMIN_EMAIL="$REPLY"
  while :; do
    printf 'Придумайте пароль (не короче 8 символов, при вводе не отображается): ' > /dev/tty
    IFS= read -rs P1 < /dev/tty; echo > /dev/tty
    printf 'Повторите пароль: ' > /dev/tty
    IFS= read -rs P2 < /dev/tty; echo > /dev/tty
    [ "$P1" = "$P2" ] || { echo "Пароли не совпали, ещё раз" > /dev/tty; continue; }
    [ ${#P1} -ge 8 ] || { echo "Слишком короткий, нужно от 8 символов" > /dev/tty; continue; }
    break
  done
  DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" DJANGO_SUPERUSER_PASSWORD="$P1" \
    "$APP_DIR/venv/bin/python" manage.py createsuperuser --noinput -v0 \
    || die "Не получилось создать администратора (возможно, email указан с ошибкой). Запустите скрипт ещё раз."
  unset P1 P2
  ok "Администратор $ADMIN_EMAIL создан"
fi

chown -R www-data:www-data "$DATA_DIR"

# ---------------------------------------------------------------------------
# Автозапуск сайта и веб-сервер с HTTPS
# ---------------------------------------------------------------------------
say "Запускаю сайт"
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Artist cabinet
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$SRC_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
EOF
chown root:www-data "$ENV_FILE"; chmod 640 "$ENV_FILE"

cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    request_body {
        max_size 50MB
    }
    reverse_proxy 127.0.0.1:8000
}
EOF

systemctl daemon-reload
systemctl enable -q --now $SERVICE
systemctl restart $SERVICE
systemctl enable -q caddy
systemctl restart caddy

# Ежедневная резервная копия в 04:30, хранится 30 дней
mkdir -p /var/backups/artist-cabinet
cat > /etc/cron.d/artist-cabinet-backup <<EOF
30 4 * * * root tar czf /var/backups/artist-cabinet/backup-\$(date +\%F).tar.gz -C $DATA_DIR . && find /var/backups/artist-cabinet -name 'backup-*.tar.gz' -mtime +30 -delete
EOF

# Открываем порты, если включён файрвол
if command -v ufw > /dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp > /dev/null; ufw allow 443/tcp > /dev/null
fi

sleep 3
if curl -fsS --max-time 5 http://127.0.0.1:8000/healthz > /dev/null; then
  ok "Сайт работает"
else
  die "Сайт не запустился. Пришлите Claude вывод команды: journalctl -u $SERVICE -n 50 --no-pager"
fi

rm -rf "$SRC_DIR.old"

cat > /dev/tty <<EOF

$(printf '\033[1;32m')════════════════════════════════════════════════════════════
  Готово!
════════════════════════════════════════════════════════════$(printf '\033[0m')

  Кабинет для артистов:   https://$DOMAIN
  Ваша админка:           https://$DOMAIN/admin/

  Первый раз сайт может открываться до минуты: в это время
  выпускается сертификат безопасности (замочек в браузере).

  Обновить сайт после изменений на GitHub — запустите ту же
  команду установки ещё раз.
  Резервные копии: /var/backups/artist-cabinet (каждую ночь)

EOF
