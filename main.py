import telnetlib
import time
import paho.mqtt.client as mqtt
import json
import crestron_parser
import os

# Crestron 8x8 Matrix Switcher to Home Assistant MQTT Bridge
# This script connects to a Crestron 8x8 Matrix Switcher via Telnet and publishes its state to Home Assistant via MQTT.
# It also listens for commands from Home Assistant to change the input/output routing of the switcher.
inputs,outputs = [],[]

PATH = '/config/'
if not os.path.exists('/.dockerenv'):
    from dotenv import load_dotenv
    load_dotenv()
    PATH = 'config/'

switcher = os.environ.get('SWITCHER_IP', 'localhost')
print("Connecting to Crestron Switcher at:", switcher)
broker = os.environ.get('MQTT_BROKER', 'localhost')
broker_port = int(os.environ.get('BROKER_PORT', 1883))  # Default MQTT port is 1883
print("Connecting to MQTT Broker at:", broker," port:", broker_port)

#Crestron Control Stuff
def getInput(name):
    for x in inputs:
        if x[0] == name:
            return x[1]
        try:
            if int(x[1]) == int(name):
                return x[0]
        except:
            pass
    return "0"
def getInputs():
    inputNames = []
    for x in inputs:
        inputNames.append(x[0])
    return inputNames
def getOutput(name):
    for x in outputs:
        if x[0] == name:
            return x
        if x[1] == name:
            return x
    return []
def getOutputs():
    outputNames = []
    for x in outputs:
        outputNames.append(x[0])
    return outputNames
def buildCommand(input,output,audio=True,video=True):
    if audio and video:
        cmd = f"SETAVROUTE {input} {output}\r\n"
        return cmd.encode()
    if audio:
        cmd = f"SETAUDIOROUTE {input} {output}\r\n"
        return cmd.encode()
    if video:
        cmd = f"SETVIDEOROUTE {input} {output}\r\n"
        return cmd.encode()
def sendCommand(input,output,audio=True,video=True):
    cmd = buildCommand(getInput(input),getOutput(output)[1],audio,video)
    tn = telnetlib.Telnet(switcher, 41795)
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(cmd)
    out = tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    out = tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"BYE\r\n")
    tn.close()
#MQTT Stuff
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker!")
        # Subscribe to a topic here after successful connection
        client.subscribe("crestroncontrol/cmd")  # Replace with your topic
        publish_message(client,"crestroncontrol/inputs",json.dumps(getInputs()))
        publish_message(client,"crestroncontrol/outputs",json.dumps(getOutputs()))
        publish_message(client,"crestroncontrol/availability","online")
        for x in outputs:
            publish_discovery(client,x)
    else:
        print(f"Failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    publish_message(client,"crestroncontrol/availability","online")
    topic = msg.topic
    payload = msg.payload.decode()  # Decode the payload (usually UTF-8)
    print(f"Received `{payload}` from `{topic}` topic")

    name = topic.replace('crestroncontrol/cmd/','')
    sendCommand(payload,name.replace('_',' '))
    output = getOutput(name.replace('_',' '))
    newoutput = output
    newoutput[2] = getInput(payload)
    outputs.append(newoutput)
    outputs.remove(output)
    publish_state(newoutput)

    
def on_disconnect(client, userdata, rc):
    print("Disconnected from MQTT Broker")

def publish_state(output):
    input = getInput(output[2])
    publish_message(client,f"crestroncontrol/state/{output[0].replace(' ','_')}", input,retain=True)
    pass
    

# Publish MQTT Discovery Message
def publish_discovery(client,output):
    name = output[0]
    discovery_payload = {
        "name": f"{name} Input",
        "unique_id": f"Crestron_{name.replace(' ','_')}_Input",
        "command_topic": f"crestroncontrol/cmd/{name.replace(' ','_')}",
        "state_topic": f"crestroncontrol/state/{name.replace(' ','_')}",
        "options": getInputs(),
        "availability_topic": "crestroncontrol/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "manufacturer": "Crestron",
            "model": "8x8 Matrix Switcher",
            "name": "Crestron Switcher",
            "identifiers": ["crestronmd8x8"],
            "sw_version": "1.0.0",
            "hw_version": "Rev A"
        },
    }
    #
    publish_message(client,f"homeassistant/select/crestroncontrol/{name.replace(' ','_')}_Input/config", json.dumps(discovery_payload),retain=True)
    client.subscribe(f"crestroncontrol/cmd/{name.replace(' ','_')}")
    print(f"Published MQTT Discovery message for {name}")
    publish_state(output)


def publish_message(client, topic, message, qos=0, retain=False):
    result = client.publish(topic, message, qos, retain)
    # result: [0, 1]
    status = result[0]
    if status == 0:
        pass
    else:
        print(f"Failed to send message to topic {topic}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,"crestron2ha")  # You can provide a client ID here if needed
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect  # Optional
client.username_pw_set("python-exlink", "nqpkMctoRefF")


try:
    client.connect(broker, broker_port, 60)  # 60 seconds keepalive
    tn = telnetlib.Telnet(switcher, 41795)
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"joinmonitorslot stop all\r\n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"cards\r\n")
    raw_cards = tn.read_until(b"DM-MD8x8>",timeout=2)
    raw_cards += tn.read_until(b"  36: DMC-FPANEL Front Panel [v2.002.0026]",timeout=10)
    cards = crestron_parser.parse_cards(raw_cards)
    tn.write(b"joingetoutserial 0x04 33 0 1 2 3 4 5 6 7 32 33 34 35 36 37 38 39 245\r\n")
    raw_io = tn.read_until(b"joingetoutserial 0x04 33 0 1 2 3 4 5 6 7 32 33 34 35 36 37 38 39 245",timeout=2)
    raw_io = tn.read_until(b"0E2E050004F5003030303030303030",timeout=2)
    tn.write(b"BYE\r\n")
    tn.close()
    inputs,outputs = crestron_parser.parseIO(cards,raw_io)
    #crestron_parser.createMonitors(outputs,tn)
    client.loop_start()  # Non -Blocking loop
    while(True):
        publish_message(client,"crestroncontrol/availability","online")
        time.sleep(10)
finally:
    time.sleep(1)
    publish_message(client,"crestroncontrol/availability","offline")
    client.disconnect()
    client.loop_stop()
