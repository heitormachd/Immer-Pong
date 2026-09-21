FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 pingpong \
    && useradd --uid 1000 --gid pingpong --no-create-home pingpong \
    && mkdir /app/data && chown pingpong:pingpong /app/data
COPY web.py storage.py ranking.py live_scoring.py tournaments.py badges.py ./
COPY templates ./templates
COPY static ./static
USER pingpong
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "1", "--access-logfile", "-", "web:create_app()"]
