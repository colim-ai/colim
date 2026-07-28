#!/usr/bin/env bash
# One-time server setup for the Colim dashboard. Run once, with sudo:
#
#     sudo ./deploy/setup_site.sh colim.ai            # apex only
#     sudo ./deploy/setup_site.sh colim.ai www.colim.ai
#
# Installs nginx + certbot, serves the generated dashboard over HTTPS, and
# leaves the webroot writable by the normal user so that redeploying later
# needs no privileges at all (see deploy/publish.sh).
#
# PREREQUISITE: an A record for each domain must already point at this box.
# Certbot validates over HTTP and will fail otherwise. Check first with:
#     dig +short <domain>

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo: sudo $0 <domain> [more domains...]" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "usage: sudo $0 <domain> [alt-domain ...]" >&2
  exit 1
fi

DOMAINS=("$@")
PRIMARY="${DOMAINS[0]}"
OWNER="${SUDO_USER:-colim}"
WEBROOT="/var/www/colim"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> domains:  ${DOMAINS[*]}"
echo "==> webroot:  $WEBROOT  (owned by $OWNER)"
echo

# --- sanity: DNS must resolve here before certbot will issue -----------------
MYIP="$(curl -fsS --max-time 10 https://api.ipify.org || echo unknown)"
# Cloudflare's proxy ("orange cloud") answers with its OWN anycast addresses,
# so the A record will not resolve to this box even when it is configured
# correctly. Certbot's HTTP-01 challenge then validates against Cloudflare
# rather than nginx, which fails unless the edge already trusts the origin.
# The reliable order is: grey-cloud (DNS only) -> issue the cert -> optionally
# re-enable the proxy with SSL mode "Full (strict)".
is_cloudflare() {
  local ip="$1"
  [[ $ip == 104.1[6-9].* || $ip == 104.2[0-7].* || $ip == 172.6[4-9].* \
     || $ip == 172.7[0-1].* || $ip == 173.245.* || $ip == 188.114.* \
     || $ip == 190.93.* || $ip == 197.234.* || $ip == 198.41.* ]]
}

for d in "${DOMAINS[@]}"; do
  GOT="$(dig +short "$d" A | tail -1)"
  if [[ -z "$GOT" ]]; then
    echo "!! $d does not resolve yet. Add an A record -> $MYIP, then re-run." >&2
    exit 1
  elif is_cloudflare "$GOT"; then
    echo "!! $d resolves to $GOT, which is a Cloudflare proxy address." >&2
    echo "   In the Cloudflare DNS panel, set the record for '$d' to DNS only" >&2
    echo "   (grey cloud), wait a minute, then re-run this script." >&2
    echo "   You can switch the proxy back on afterwards -- set SSL/TLS mode to" >&2
    echo "   'Full (strict)' once the origin certificate exists." >&2
    exit 1
  elif [[ "$GOT" != "$MYIP" && "$MYIP" != "unknown" ]]; then
    echo "!! $d resolves to $GOT but this box is $MYIP." >&2
    echo "   Fix DNS (or wait for propagation) before requesting a certificate." >&2
    exit 1
  fi
  echo "    $d -> $GOT  ok"
done
echo

echo "==> installing nginx + certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null

echo "==> preparing webroot"
mkdir -p "$WEBROOT"
chown -R "$OWNER":"$OWNER" "$WEBROOT"
# Seed it so nginx has something to serve before the first publish.
if [[ -f "$REPO_DIR/site/index.html" ]]; then
  install -o "$OWNER" -g "$OWNER" -m 0644 "$REPO_DIR/site/index.html" "$WEBROOT/index.html"
fi

echo "==> writing nginx site"
SERVER_NAMES="${DOMAINS[*]}"
cat > /etc/nginx/sites-available/colim <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAMES};

    root ${WEBROOT};
    index index.html;

    # The dashboard is a single static file with no scripts. Lock it down.
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location / {
        try_files \$uri \$uri/ =404;
    }

    # Serve the methodology and census report as readable plain text rather
    # than prompting a download.
    location ~* \.md\$ {
        default_type text/plain;
        charset utf-8;
    }

    access_log /var/log/nginx/colim.access.log;
    error_log  /var/log/nginx/colim.error.log;
}
NGINX

ln -sf /etc/nginx/sites-available/colim /etc/nginx/sites-enabled/colim
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
echo "    nginx serving http://${PRIMARY}/"

echo "==> requesting TLS certificate"
CERT_ARGS=()
for d in "${DOMAINS[@]}"; do CERT_ARGS+=(-d "$d"); done
if certbot --nginx --non-interactive --agree-tos --redirect \
     --register-unsafely-without-email "${CERT_ARGS[@]}"; then
  echo "    certificate installed; HTTP redirects to HTTPS"
else
  echo "!! certbot failed. The site is still live over plain HTTP." >&2
  echo "   Re-run once DNS has propagated:" >&2
  echo "   sudo certbot --nginx ${CERT_ARGS[*]}" >&2
fi

# Ubuntu's certbot package installs a renewal timer automatically; confirm it.
systemctl list-timers 'certbot*' --no-pager | tail -2 || true

echo
echo "==> done.  https://${PRIMARY}/"
echo "    Redeploy any time with:  ./deploy/publish.sh   (no sudo needed)"
