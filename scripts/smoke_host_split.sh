#!/bin/bash
echo "=== A: roobico.com/dashboard (expect 301 -> app.roobico.com) ==="
curl -sI -H 'Host: roobico.com' -H 'X-Forwarded-Proto: https' http://127.0.0.1/dashboard | head -n 8
echo
echo "=== B: app.roobico.com/ (expect 301 -> roobico.com) ==="
curl -sI -H 'Host: app.roobico.com' -H 'X-Forwarded-Proto: https' http://127.0.0.1/ | head -n 8
echo
echo "=== C: roobico.com/forgot-password (expect 200) ==="
curl -sI -H 'Host: roobico.com' -H 'X-Forwarded-Proto: https' http://127.0.0.1/forgot-password | head -n 4
echo
echo "=== D: app.roobico.com/dashboard no-session (expect 302) ==="
curl -sI -H 'Host: app.roobico.com' -H 'X-Forwarded-Proto: https' http://127.0.0.1/dashboard | head -n 8
