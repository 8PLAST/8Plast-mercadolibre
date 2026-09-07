FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 APP_ENV=cloud TZ=America/Argentina/Buenos_Aires
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENTRYPOINT ["python", "/app/cloud_start.py"]
CMD []
