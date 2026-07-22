FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch_data.py fit_forecasts.py app.py ./

# data/ is mounted at runtime so the cache persists across container restarts
VOLUME ["/app/data"]

EXPOSE 8052

# Honors DASH_URL_BASE_PATHNAME so the probe still hits a real route if the
# app is ever served at a sub-path behind a reverse proxy
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:8052' + os.environ.get('DASH_URL_BASE_PATHNAME', '/'), timeout=4)"

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8052", "app:server"]
