FROM python:3.12-alpine
RUN pip install --no-cache-dir --retries 10 docker
COPY picker.py /picker.py
ENTRYPOINT ["python", "-u", "/picker.py"]
