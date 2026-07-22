#!/bin/sh
# Corrige les droits du volume persistant puis lance l'app en non-root.
# Repli : si setpriv n'est pas disponible, on lance en root comme avant.
set -e

if command -v setpriv >/dev/null 2>&1 && id maltai >/dev/null 2>&1; then
    chown -R maltai:maltai /app/data 2>/dev/null || true
    exec setpriv --reuid=maltai --regid=maltai --init-groups "$@"
fi

exec "$@"
