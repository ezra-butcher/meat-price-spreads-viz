FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch_data.py fit_forecasts.py app.py ./

# data/ is mounted at runtime so the cache persists across container restarts
VOLUME ["/app/data"]

EXPOSE 8051

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8051/', timeout=4)"

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8051", "app:server"]
