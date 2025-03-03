import telnetlib
import time
import paho.mqtt.client as mqtt
import json

switcherIP = "192.168.1.198"
inputs = [["Bedroom Apple TV","1"],["Blu Ray Player","2"],["Input 3","3"],["Input 4","4"],["Input 5","5"],["Input 6","6"],["Input 7","7"],["No Input","0"]]
outputs = [["Bedroom TV","17","No Input"],["Basement TV","18","No Input"],["Output 3","19","No Input"],["Output 4","20","No Input"],["Office Monitor","21","No Input"],["Output 6","22","No Input"],["Servers","23","No Input"],["Output 8","24","No Input"]]
broker_address = "192.168.1.5"  # Replace with your broker's address
port = 1883  # Default MQTT port

#Crestron Control Stuff
def getInput(name):
    for x in inputs:
        if x[0] == name:
            return x[1]
        if x[1] == name:
            return x[0]
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
        cmd = f"SETAUDIOVROUTE {input} {output}\r\n"
        return cmd.encode()
    if video:
        cmd = f"SETVIDEOROUTE {input} {output}\r\n"
        return cmd.encode()
def sendCommand(input,output,audio=True,video=True):
    cmd = buildCommand(getInput(input),getOutput(output)[1],audio,video)
    tn.write(cmd)
    tn.read_until(b"DM-MD8x8>",timeout=2)
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
    newoutput[2] = payload
    outputs.append(newoutput)
    outputs.remove(output)
    publish_state(newoutput)

    
def on_disconnect(client, userdata, rc):
    print("Disconnected from MQTT Broker")

def publish_state(output):
    publish_message(client,f"crestroncontrol/state/{output[0].replace(' ','_')}", output[2],retain=True)
    

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
    tn = telnetlib.Telnet(switcherIP, 41795)
    client.connect(broker_address, port, 60)  # 60 seconds keepalive
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"/r/n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"/r/n")
    tn.read_until(b"DM-MD8x8>",timeout=2)
    tn.write(b"/r/n")
    client.loop_start()  # Non -Blocking loop
    while(True):
        publish_message(client,"crestroncontrol/availability","online")
        time.sleep(5)
finally:
    time.sleep(1)
    publish_message(client,"crestroncontrol/availability","offline")
    client.disconnect()
    client.loop_stop()
    tn.write(b"BYE")
    tn.close()
