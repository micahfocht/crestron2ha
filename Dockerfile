FROM python:3.9
RUN pip install paho-mqtt
ADD main.py .
ADD crestron_parser.py .
CMD ["python3", "-u","./main.py"] 
