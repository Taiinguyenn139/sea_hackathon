#!/usr/bin/env bash
set -euo pipefail
# Bootstrap a dedicated Debian VM; attached data disk is never auto-deleted.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io curl ca-certificates python3
DATA_DEVICE=/dev/disk/by-id/google-harness-data
test -b "$DATA_DEVICE"
if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then
  # Only a newly created, entirely blank disk may be formatted.
  test -z "$(wipefs -n --noheadings -o TYPE "$DATA_DEVICE")"
  mkfs.ext4 -F "$DATA_DEVICE"
fi
test "$(blkid -s TYPE -o value "$DATA_DEVICE")" = ext4
install -d /var/lib/memory-harness /etc/memory-harness
UUID=$(blkid -s UUID -o value "$DATA_DEVICE")
if ! grep -q "UUID=$UUID " /etc/fstab; then
  printf 'UUID=%s /var/lib/memory-harness ext4 defaults,nofail 0 2\n' "$UUID" >> /etc/fstab
fi
mountpoint -q /var/lib/memory-harness || mount /var/lib/memory-harness
# At subsequent boots, containers must wait for the persistent disk.
install -d /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/harness-data.conf <<'EOF'
[Unit]
RequiresMountsFor=/var/lib/memory-harness
EOF
systemctl daemon-reload
systemctl enable --now docker
install -d -o 10001 -g 10001 /var/lib/memory-harness/data
install -d -m 700 /var/lib/memory-harness/backups
docker network inspect memory-harness >/dev/null 2>&1 || docker network create memory-harness
# runtime.env is provisioned separately with the selected secret-store mechanism.
# Do not start an unauthenticated staff/admin console before it exists.
echo 'Bootstrap complete; runtime secrets and first release still required.'
