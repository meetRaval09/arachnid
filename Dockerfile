# Arachnid in one box: Python, the real browser for the safety gate, git (who changed what) and esbuild (JavaScript).
#   docker run --rm -v "C:\my\project:/project" ghcr.io/meetraval09/arachnid scan
# Easier: the arachnid.cmd (Windows) / arachnid.sh (Mac, Linux) helpers next to this file type that for you.
FROM ubuntu:24.04

ARG PLAYWRIGHT_VERSION=1.63.0
ARG ESBUILD_VERSION=0.25.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ARACHNID_ESBUILD_DIR=/opt/esbuild \
    ARACHNID_IN_DOCKER=1 \
    HOME=/tmp

# Python, git, tini (lets Ctrl+C and "docker stop" end Arachnid cleanly) and Node (only to run esbuild)
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates tini nodejs npm \
 && mkdir -p /opt/esbuild && cd /opt/esbuild \
 && npm install --no-audit --no-fund --omit=dev "esbuild@${ESBUILD_VERSION}" \
 && apt-get purge -y npm && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/.npm \
 && git config --system safe.directory '*'

# The optional extras: the safety gate's browser, picture checks, encrypted GitHub secrets
RUN python3 -m venv /opt/venv \
 && pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" "pillow>=10" "pynacl>=1.5" \
 && playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /ms-playwright

WORKDIR /app
COPY . /app
RUN mkdir -p /app/reports /app/backups /project \
 && chmod 0777 /app/reports /app/backups \
 && chmod 0755 /app/docker/entrypoint.sh \
 && python -m compileall -q /app

# Your project goes in /project.  Results and backups (for undo) live in /app/reports and /app/backups -
# the helpers keep them in a Docker volume per project, so "undo" still works next time.
EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD ["help"]
