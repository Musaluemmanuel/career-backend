FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["sh", "-c", "python manage.py migrate --noinput --fake-initial && gunicorn careerpath.wsgi:application --bind 0.0.0.0:${PORT:-10000} --timeout 300"]
