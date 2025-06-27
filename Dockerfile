FROM python:3.10-alpine

ADD . /opt/exporter
WORKDIR /opt/exporter

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-u", "./src/swarm_exporter_prom.py"]
