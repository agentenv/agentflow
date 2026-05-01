FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["agentflow", "serve", "--host", "0.0.0.0", "--port", "8000"]
