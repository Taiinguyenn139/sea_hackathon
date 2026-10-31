#!/usr/bin/env bash
set -euo pipefail
DOMAIN=${1:?Public hostname required}
[[ "$DOMAIN" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || exit 2
install -d /var/lib/memory-harness/release /etc/memory-harness /var/lib/memory-harness/caddy-data /var/lib/memory-harness/caddy-config
printf '%s\n' "$DOMAIN" > /etc/memory-harness/domain
cat > /etc/memory-harness/Caddyfile <<EOF
$DOMAIN {
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        X-Frame-Options DENY
    }
    @ready file {
        root /release
        try_files ready
    }
    handle /healthz {
        reverse_proxy memory-harness-app:8080
    }
    handle @ready {
        reverse_proxy memory-harness-app:8080
    }
    handle {
        respond "Deployment in progress. Please retry shortly." 503
    }
}
EOF
docker rm -f memory-harness-proxy >/dev/null 2>&1 || true
docker run -d --name memory-harness-proxy --restart unless-stopped \
  --network memory-harness -p 80:80 -p 443:443 \
  --mount type=bind,source=/var/lib/memory-harness/release,target=/release,readonly \
  --mount type=bind,source=/etc/memory-harness/Caddyfile,target=/etc/caddy/Caddyfile,readonly \
  --mount type=bind,source=/var/lib/memory-harness/caddy-data,target=/data \
  --mount type=bind,source=/var/lib/memory-harness/caddy-config,target=/config \
  --log-opt max-size=10m --log-opt max-file=3 caddy:2.10-alpine >/dev/null
echo "HTTPS proxy configured for $DOMAIN"
