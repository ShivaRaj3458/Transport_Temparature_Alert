FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 COLDCHAIN_DB=/app/data/coldchain.sqlite3
WORKDIR /app
COPY app.py ./app.py
COPY starter_kit/data/sensor_readings.json ./starter_kit/data/sensor_readings.json
COPY web ./web
RUN mkdir -p /app/data && useradd --uid 10001 --no-create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
CMD ["python", "app.py"]
