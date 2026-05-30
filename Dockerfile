FROM python:3.12-slim

WORKDIR /app

COPY ai_article_digest.py digest_config.example.json ./

CMD ["python", "./ai_article_digest.py", "--config", "./digest_config.json"]
