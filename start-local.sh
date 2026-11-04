#!/usr/bin/env bash
# One-command source-local stack. Docker wrappers remain unchanged.
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY="$ROOT/app/.venv/bin/python"
ACTION="${1:-start}"
PORT="${MARKETWATCH_PORT:-8787}"
case "$ACTION" in
  start|stop|status|restart) ;;
  -h|--help)
    printf '%s\n' 'Uso: ./start-local.sh [start|status|stop|restart]' \
      'Avvia monitor, GUI e worker AI in background. Non avvia server GPU.' \
      'MARKETWATCH_PORT: porta GUI (default 8787).'
    exit 0 ;;
  *) printf 'Azione non valida: usa start, status, stop o restart.\n' >&2; exit 2 ;;
esac
if [[ ! "$PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
  printf 'MARKETWATCH_PORT deve essere una porta tra 1 e 65535.\n' >&2
  exit 2
fi
PORT=$((10#$PORT))
if [[ ! -x "$PY" ]]; then
  printf 'Ambiente Python mancante: app/.venv. Vedi docs/OPERATIONS.md.\n' >&2
  exit 1
fi
# Do not inherit Hermes' interpreter-specific Python paths.
py() { env -u PYTHONPATH -u PYTHONHOME "$PY" "$@"; }
DB="$(py -c 'from pathlib import Path; from subitoo.config import get_settings; print(Path(get_settings().db_path).expanduser().resolve())')"
STATE="$ROOT/data/local-stack"
mkdir -p "$STATE"
command -v flock >/dev/null || { printf 'Comando flock non disponibile.\n' >&2; exit 1; }
exec 9>"$STATE/launcher.lock"
flock -w 15 9 || { printf 'Un altro comando di avvio/arresto è in corso.\n' >&2; exit 1; }

# Never trust a PID file by itself: verify cwd, module, command and worker DB.
# Adoption lets this launcher reuse the existing source-local GUI/monitor safely.
matching_pids() {
  py -c '
import os, pathlib, sys
root, role, db, port = sys.argv[1:]
for entry in pathlib.Path("/proc").iterdir():
    if not entry.name.isdecimal(): continue
    try:
        if str((entry/"cwd").resolve()) != root: continue
        args=(entry/"cmdline").read_bytes().decode().split("\0")[:-1]
        i=args.index("-m"); module=args[i+1]
        if role=="worker":
            if module!="subitoo.ai.worker": continue
            if "--db" in args and str(pathlib.Path(args[args.index("--db")+1]).resolve())!=db: continue
        else:
            if module!="subitoo.cli" or args[i+2] != ("serve" if role=="monitor" else "gui"): continue
            if role=="gui":
                actual=args[args.index("--port")+1] if "--port" in args else "8787"
                if actual!=port: continue
        os.kill(int(entry.name),0)
        print(entry.name)
    except (OSError,ValueError,IndexError,UnicodeError): pass
' "$ROOT" "$1" "$DB" "$PORT"
}
current_pid() {
  local pid
  pid="$(matching_pids "$1" | sort -n | tr '\n' ' ')"
  # More than one matching service is an existing operator issue; do not add more.
  printf '%s' "${pid%% *}"
}
ready() {
  py -c '
import json,sys,urllib.request
try:
    request=urllib.request.Request("http://127.0.0.1:"+sys.argv[1]+"/api/ai/state")
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=2) as response:
        data=json.load(response)
    assert isinstance(data,dict)
except Exception: sys.exit(1)
' "$PORT" >/dev/null 2>&1
}
stop_one() {
  local role="$1" pid
  pid="$(current_pid "$role")"
  if [[ -z "$pid" ]]; then
    rm -f -- "$STATE/$role.pid"
    printf '%s: non attivo.\n' "$role"
    return
  fi
  # Stop the verified same-project service, not any unrelated stale saved PID.
  kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..50}; do
    if [[ "$(current_pid "$role")" != "$pid" ]]; then break; fi
    sleep 0.1
  done
  if [[ "$(current_pid "$role")" == "$pid" ]]; then
    printf '%s: arresto forzato dopo attesa; i job interrotti saranno recuperati.\n' "$role"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f -- "$STATE/$role.pid"
  printf '%s: arrestato.\n' "$role"
}
stop_all() { stop_one worker; stop_one gui; stop_one monitor; }
if [[ "$ACTION" == stop ]]; then stop_all; exit 0; fi
if [[ "$ACTION" == restart ]]; then stop_all; ACTION=start; fi
if [[ "$ACTION" == status ]]; then
  missing=0
  for role in monitor gui worker; do
    pid="$(current_pid "$role")"
    if [[ -n "$pid" ]]; then printf '%s: attivo (PID %s).\n' "$role" "$pid"
    else printf '%s: non attivo.\n' "$role"; missing=1; fi
  done
  printf 'Interfaccia: http://127.0.0.1:%s\nLog: %s\n' "$PORT" "$STATE"
  exit "$missing"
fi

NEW_ROLES=()
cleanup_failed_start() {
  local result=$?
  if (( result != 0 )); then
    printf 'Avvio non completato. Controlla i log in %s\n' "$STATE" >&2
    # Roll back only processes created by this attempt, not reused services.
    for role in "${NEW_ROLES[@]}"; do stop_one "$role"; done
  fi
}
trap cleanup_failed_start EXIT
start_one() {
  local role="$1"; shift
  local pid
  pid="$(current_pid "$role")"
  if [[ -n "$pid" ]]; then
    printf '%s: già attivo, riutilizzato (PID %s).\n' "$role" "$pid"
  else
    nohup env -u PYTHONPATH -u PYTHONHOME "$PY" "$@" \
      </dev/null >>"$STATE/$role.log" 2>&1 9>&- &
    pid=$!
    NEW_ROLES+=("$role")
    printf '%s: avviato (PID %s).\n' "$role" "$pid"
  fi
  printf '%s\n' "$pid" >"$STATE/$role.pid"
}
# Upgrade an already-running old same-project GUI that cannot serve the AI panel.
if [[ -n "$(current_pid gui)" ]] && ! ready; then
  printf 'Riavvio della GUI esistente per caricare il nuovo pannello AI.\n'
  stop_one gui
fi
start_one monitor -m subitoo.cli serve
start_one gui -m subitoo.cli gui --port "$PORT" --no-open-browser
start_one worker -m subitoo.ai.worker --db "$DB"
for _ in {1..30}; do
  if ready; then break; fi
  sleep 0.5
done
if ! ready; then printf 'GUI non pronta o porta %s occupata.\n' "$PORT" >&2; exit 1; fi
for role in monitor gui worker; do
  if [[ -z "$(current_pid "$role")" ]]; then
    printf '%s si è arrestato: consulta %s/%s.log\n' "$role" "$STATE" "$role" >&2
    exit 1
  fi
done
trap - EXIT
printf '\nTutto avviato in background. Puoi chiudere questo terminale.\n'
printf 'Apri: http://127.0.0.1:%s\nLog: %s\n' "$PORT" "$STATE"
printf 'Stato: ./start-local.sh status\nArresto: ./start-local.sh stop\n'
printf 'Il worker usa il modello configurato; nessun nuovo server GPU viene avviato.\n'
printf 'Se necessario, abilita AI nelle impostazioni della pagina.\n'
