#!/bin/sh
# Turns "docker run ... arachnid <command>" into the right Arachnid command, always aimed at /project.
set -e
cmd="${1:-help}"
[ $# -gt 0 ] && shift

need_project() {
    if [ -z "$(ls -A /project 2>/dev/null)" ]; then
        echo "Arachnid cannot see your project."
        echo "Share it with the container by adding:   -v \"<your project folder>:/project\""
        echo "(or use the arachnid.cmd / arachnid.sh helper, which does it for you)."
        exit 2
    fi
}

case "$cmd" in
    dashboard)
        need_project
        echo "Open this address in your browser:  http://127.0.0.1:${ARACHNID_PORT:-8765}/"
        echo "(Press Ctrl+C here to stop the dashboard.)"
        exec python /app/dashboard.py /project --host 0.0.0.0 --port 8765 --no-browser "$@" ;;
    scan|fix|crawl|insights|triage|advise|slim|netcheck|size|leaks|apply-approved)
        need_project
        exec python /app/arachnid.py "$cmd" /project "$@" ;;
    undo|proposals|approve|reject|selftest)
        exec python /app/arachnid.py "$cmd" "$@" ;;
    email|preview)
        need_project
        exec python /app/notify.py "$cmd" /project "$@" ;;
    accuracy)
        exec python /app/qa_accuracy.py --esbuild /opt/esbuild/node_modules "$@" ;;
    shell)
        exec /bin/bash ;;
    version)
        python - <<'PY'
import platform, playwright, subprocess
print("Arachnid image | Python", platform.python_version(), "| Playwright", __import__("importlib.metadata").metadata.version("playwright"))
print("esbuild", subprocess.run(["/opt/esbuild/node_modules/esbuild/bin/esbuild", "--version"], capture_output=True, text=True).stdout.strip())
PY
        ;;
    help|-h|--help)
        cat <<'TXT'
Arachnid - finds problems in your project and fixes only the ones it can prove are harmless.

  scan        look only, change nothing
  fix         fix what is provably safe (backed up first)
  undo        put the last fix back (repeat to go further back)
  dashboard   the control room in your browser (http://127.0.0.1:8765)
  slim        shorter code, the SAME product: proven-identical shorter versions as proposals
              (slim --ai: also ask an AI with the Ponytail rules - pass -e ANTHROPIC_API_KEY or -e ARACHNID_AI_CMD)
  proposals   list suggestions waiting for approval;  approve / reject <ids>
  selftest    prove the whole chain works inside this container
  accuracy    the planted-problem test (30 must be found, decoys must stay quiet)
  netcheck, size, leaks, advise, insights, triage, crawl   the extra checks
  version     show what is inside the image

Your project must be shared as /project:
  docker run --rm -it -v "C:\path\to\project:/project" ghcr.io/meetraval09/arachnid scan
TXT
        ;;
    *)
        echo "Unknown command: $cmd   (try: help)"
        exit 2 ;;
esac
