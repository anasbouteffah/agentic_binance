FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config.yaml persona.md ./
# Mount ./data to keep history across runs; pass keys with --env-file .env
VOLUME ["/app/data"]
ENTRYPOINT ["w2e"]
CMD ["run"]
