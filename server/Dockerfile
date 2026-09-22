FROM python:3.12-slim
WORKDIR /app
COPY lobby_server.py protocol.py pair_relay.py login_hash.py ./
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1
USER nobody
# settings come from environment variables (see .env.example / docker-compose.yml)
ENTRYPOINT ["python", "lobby_server.py", "--env", "/dev/null"]
