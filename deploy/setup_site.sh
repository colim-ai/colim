#!/usr/bin/env bash
# One-time server setup for the Colim dashboard. Run once, with sudo:
#
#     sudo ./deploy/setup_site.sh colim.ai colim.ai www.colim.ai
#
# Installs nginx + certbot, serves the generated dashboard over HTTPS, and
# leaves the webroot writable by the normal user so that redeploying later
# needs no privileges at all (see deploy/publish.sh).
#
# TWO VALIDATION MODES
#
#   HTTP-01 (default). Requires each A record to point AT THIS BOX, which on
#   Cloudflare means the proxy must be off (grey cloud). Certbot serves a
#   challenge file on port 80; if Cloudflare's edge answers instead, the proof
#   never reaches us and issuance fails.
#
#   DNS-01 (--cloudflare-token FILE). Proves control by writing a TXT record
#   through the Cloudflare API instead of serving anything. This works with the
#   proxy LEFT ON, which is what you actually want for a public site, and
#   avoids toggling DNS during a launch. Create a token at
#   https://dash.cloudflare.com/profile/api-tokens with the "Edit zone DNS"
#   template scoped to colim.ai, then:
#
#     printf 'dns_cloudflare_api_token = YOUR_TOKEN\n' > ~/cf.ini
#     chmod 600 ~/cf.ini
#     sudo ./deploy/setup_site.sh --cloudflare-token ~/cf.ini colim.ai www.colim.ai
#
#   With DNS-01, set Cloudflare SSL/TLS mode to "Full (strict)" afterwards so
#   the edge validates our real certificate.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo: sudo $0 <domain> [more domains...]" >&2
  exit 1
fi
CF_TOKEN_FILE=""
if [[ "${1:-}" == "--cloudflare-token" ]]; then
  CF_TOKEN_FILE="${2:-}"
  shift 2
  if [[ ! -r "$CF_TOKEN_FILE" ]]; then
    echo "cannot read Cloudflare token file: $CF_TOKEN_FILE" >&2
    exit 1
  fi
fi

if [[ $# -lt 1 ]]; then
  echo "usage: sudo $0 [--cloudflare-token FILE] <domain> [alt-domain ...]" >&2
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

if [[ -n "$CF_TOKEN_FILE" ]]; then
  # DNS-01 proves control via the Cloudflare API, so where the A record points
  # is irrelevant -- the proxy may stay on. Only check the name exists at all.
  echo "==> DNS-01 validation via Cloudflare API (proxy may remain enabled)"
  for d in "${DOMAINS[@]}"; do
    GOT="$(dig +short "$d" A | tail -1)"
    [[ -z "$GOT" ]] && { echo "!! $d has no A record at all; add one first." >&2; exit 1; }
    echo "    $d -> $GOT  (proxied: $(is_cloudflare "$GOT" && echo yes || echo no))"
  done
  echo
fi

for d in "${DOMAINS[@]}"; do
  [[ -n "$CF_TOKEN_FILE" ]] && break   # handled above
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
    echo >&2
    echo "   OR keep the proxy on and validate over DNS instead:" >&2
    echo "     printf 'dns_cloudflare_api_token = TOKEN\\n' > ~/cf.ini && chmod 600 ~/cf.ini" >&2
    echo "     sudo $0 --cloudflare-token ~/cf.ini ${DOMAINS[*]}" >&2
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
PKGS=(nginx certbot python3-certbot-nginx)
[[ -n "$CF_TOKEN_FILE" ]] && PKGS+=(python3-certbot-dns-cloudflare)
apt-get install -y -qq "${PKGS[@]}" >/dev/null

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

if [[ -n "$CF_TOKEN_FILE" ]]; then
  # certbot refuses a group/world-readable credentials file, and it is right to.
  chmod 600 "$CF_TOKEN_FILE"
  CERT_CMD=(certbot certonly --dns-cloudflare
            --dns-cloudflare-credentials "$CF_TOKEN_FILE"
            --dns-cloudflare-propagation-seconds 30)
else
  CERT_CMD=(certbot --nginx --redirect)
fi

if "${CERT_CMD[@]}" --non-interactive --agree-tos \
     --register-unsafely-without-email "${CERT_ARGS[@]}"; then
  echo "    certificate obtained"
  if [[ -n "$CF_TOKEN_FILE" ]]; then
    # certonly does not touch nginx, so wire the cert in explicitly.
    certbot install --cert-name "$PRIMARY" --nginx --non-interactive --redirect \
      || echo "!! installed cert but could not configure nginx automatically" >&2
  fi
  echo "    HTTP redirects to HTTPS"
else
  echo "!! certbot failed. The site is still live over plain HTTP." >&2
  echo "   Fix the cause and re-run; nothing else needs redoing." >&2
fi

# Ubuntu's certbot package installs a renewal timer automatically; confirm it.
systemctl list-timers 'certbot*' --no-pager | tail -2 || true

echo
echo "==> done.  https://${PRIMARY}/"
echo "    Redeploy any time with:  ./deploy/publish.sh   (no sudo needed)"
