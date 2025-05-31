FROM --platform=$BUILDPLATFORM python:3.13.3

LABEL Maintainer="micahfocht"

ENV SWITCHER_IP=''
ENV MQTT_BROKER=''

RUN pip install paho-mqtt exscript

ADD main.py .
ADD crestron_parser.py .

SHELL ["/bin/bash", "-c"]

CMD python3 -u main.py