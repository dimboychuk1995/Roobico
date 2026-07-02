# Gunicorn config для Roobico (прод).
#
# Подключение в systemd-юните (/etc/systemd/system/roobico.service):
#   ExecStart=/home/deploy/Roobico/venv/bin/gunicorn \
#       -c /home/deploy/Roobico/deploy/gunicorn.conf.py run:app
#
# После изменения юнита: systemctl daemon-reload && systemctl restart roobico

import multiprocessing

# nginx проксирует на этот unix-сокет (см. nginx_roobico.conf).
bind = "unix:/home/deploy/Roobico/roobico.sock"

# Классическая формула для sync-воркеров. На маленьком дроплете
# ограничиваем сверху, чтобы не съесть память Mongo-запросами.
workers = min(multiprocessing.cpu_count() * 2 + 1, 5)
threads = 2

# Согласовано с nginx proxy_read_timeout 120s: gunicorn должен жить
# дольше, чем nginx ждёт ответ, иначе клиент получает пустой 502.
timeout = 120
graceful_timeout = 30
keepalive = 5

# Перезапуск воркера после N запросов — страховка от медленных утечек
# памяти (PyMuPDF/matplotlib/xhtml2pdf этим грешат). Jitter, чтобы
# воркеры не перезапускались одновременно.
max_requests = 1000
max_requests_jitter = 100

# Логи в stdout/stderr → systemd journal (journalctl -u roobico).
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Не логируем healthcheck-шум.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss'
capture_output = True
