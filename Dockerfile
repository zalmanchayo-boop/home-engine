FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/var/data UPLOAD_DIR=/var/data/uploads PORT=10000 PY=python
RUN mkdir -p /var/data/uploads && chmod +x run.sh
EXPOSE 10000
CMD ["sh","-c","mkdir -p $UPLOAD_DIR && ./run.sh"]
