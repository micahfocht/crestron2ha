FROM python:3.9
RUN pip install paho-mqtt
ADD main.py .
CMD ["python3", "./main.py"] 
