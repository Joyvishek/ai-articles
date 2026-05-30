FROM python:3.12-slim

WORKDIR /app

COPY main.py digest_config.json ./

CMD ["python", "./main.py", "--config", "./digest_config.json"]
