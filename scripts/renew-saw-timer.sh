#!/usr/bin/env bash
set -euo pipefail
umask 077
expiry='__EXPIRY__'
nonce='__NONCE__'
expiry_seconds=$(date -u -d "$expiry" +%s)
now=$(date -u +%s)
[[ "$expiry_seconds" -gt "$((now + 300))" && "$expiry_seconds" -le "$((now + 7200))" ]]
[[ -x /usr/local/sbin/learningnemo-saw-expire ]]
[[ -f /etc/systemd/system/learningnemo-saw-expire.timer ]]
backup="/var/lib/learningnemo-saw/renewal-$nonce"
install -d -m 0700 "$backup"
cp -a /etc/systemd/system/learningnemo-saw-expire.timer "$backup/"
cp -a /etc/systemd/system/learningnemo-saw-expire.service "$backup/"
cp -a /usr/local/sbin/learningnemo-saw-expire "$backup/"
cat > /usr/local/sbin/learningnemo-saw-expire <<'PRESERVE'
#!/usr/bin/env bash
set -euo pipefail
for sandbox in planning-demo execution-demo probe-demo; do
	runuser -u sawadmin -- env HOME=/home/sawadmin XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus timeout 30 openshell --gateway openshell sandbox stop "$sandbox" >/dev/null 2>&1 || true
done
printf 'Workspace lease ended; sandbox disks and Azure resources retained for testing.\n'
PRESERVE
chmod 0700 /usr/local/sbin/learningnemo-saw-expire
[[ ! -f /var/lib/learningnemo-saw/readiness.json ]] || cp -a /var/lib/learningnemo-saw/readiness.json "$backup/"
journalctl -u learningnemo-saw-expire.service --no-pager -n 50 > "$backup/expiry-journal.txt"
systemctl show learningnemo-saw-expire.timer > "$backup/old-timer.txt"
systemctl stop learningnemo-saw-expire.timer
calendar=$(date -u -d "$expiry" '+%Y-%m-%d %H:%M:%S UTC')
printf '[Unit]\nDescription=Enforce bounded renewed SAW lifetime\n[Timer]\nOnCalendar=%s\nAccuracySec=1s\nPersistent=true\nUnit=learningnemo-saw-expire.service\n[Install]\nWantedBy=timers.target\n' "$calendar" > /etc/systemd/system/learningnemo-saw-expire.timer
systemctl daemon-reload
systemctl enable --now learningnemo-saw-expire.timer >/dev/null
systemctl is-active --quiet learningnemo-saw-expire.timer
next=$(systemctl show learningnemo-saw-expire.timer -p NextElapseUSecRealtime --value)
[[ "$(date -u -d "$next" +%s)" -eq "$expiry_seconds" ]]
printf 'PASS TIMER_%s %s\n' "$nonce" "$expiry_seconds"