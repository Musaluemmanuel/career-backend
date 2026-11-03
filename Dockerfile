FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["sh", "-c", "gunicorn secured_app:app --bind 0.0.0.0:${PORT:-10000} --timeout 300"]
