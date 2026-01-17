from Exscript.protocols import telnetlib
import time
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion  # type: ignore
import json
import crestron_parser
import os
import logging
from typing import List, Tuple, Optional, Any
from contextlib import contextmanager

# Crestron 8x8 Matrix Switcher to Home Assistant MQTT Bridge
# This script connects to a Crestron 8x8 Matrix Switcher via Telnet and publishes its state to Home Assistant via MQTT.
# It also listens for commands from Home Assistant to change the input/output routing of the switcher.

# Constants
CRESTRON_TELNET_PORT = 41795
MQTT_KEEPALIVE = 60
AVAILABILITY_CHECK_INTERVAL = 10
MAX_COMMAND_RETRIES = 3
RETRY_DELAY_BASE = 1.0  # Base delay in seconds for exponential backoff

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global state
inputs: List[Tuple[str, str]] = []
outputs: List[Tuple[str, str, str]] = []  # [name, id, current_input]

PATH = '/config/'
if not os.path.exists('/.dockerenv'):
    from dotenv import load_dotenv
    load_dotenv()
    PATH = 'config/'

switcher = os.environ.get('SWITCHER_IP', 'localhost')
logger.info(f"Connecting to Crestron Switcher at: {switcher}")
broker = os.environ.get('MQTT_BROKER', 'localhost')
broker_port = int(os.environ.get('BROKER_PORT', 1883))  # Default MQTT port is 1883
mqtt_username = os.environ.get('MQTT_USERNAME', '')
mqtt_password = os.environ.get('MQTT_PASSWORD', '')
logger.info(f"Connecting to MQTT Broker at: {broker} port: {broker_port}")

# Crestron Control Functions
def getInput(name: str) -> str:
    """
    Get input ID by name, or name by ID.
    
    Args:
        name: Input name or ID
        
    Returns:
        Input ID if name provided, input name if ID provided, "0" if not found
    """
    for input_item in inputs:
        if input_item[0] == name:
            return input_item[1]
        try:
            if int(input_item[1]) == int(name):
                return input_item[0]
        except (ValueError, TypeError):
            pass
    return "999"

def getInputs() -> List[str]:
    """Get list of all input names."""
    return [input_item[0] for input_item in inputs]

def getOutput(name: str) -> Optional[Tuple[str, str, str]]:
    """
    Get output by name or ID.
    
    Args:
        name: Output name or ID
        
    Returns:
        Output tuple [name, id, current_input] or None if not found
    """
    for output_item in outputs:
        if output_item[0] == name or output_item[1] == name:
            return output_item
    return None

def getOutputs() -> List[str]:
    """Get list of all output names."""
    return [output_item[0] for output_item in outputs]

def buildCommand(input_id: str, output_id: str, audio: bool = True, video: bool = True) -> bytes:
    """
    Build a Crestron routing command.
    
    Args:
        input_id: Input ID
        output_id: Output ID
        audio: Include audio routing
        video: Include video routing
        
    Returns:
        Encoded command bytes
    """
    if audio and video:
        cmd = f"SETAVROUTE {input_id} {output_id}\r\n"
    elif audio:
        cmd = f"SETAUDIOROUTE {input_id} {output_id}\r\n"
    elif video:
        cmd = f"SETVIDEOROUTE {input_id} {output_id}\r\n"
    else:
        raise ValueError("At least one of audio or video must be True")
    return cmd.encode()

@contextmanager
def telnet_connection():
    """
    Context manager for telnet connections to ensure proper cleanup.
    
    Yields:
        Telnet connection object
    """
    tn = None
    try:
        tn = telnetlib.Telnet(switcher, CRESTRON_TELNET_PORT, timeout=5)
        yield tn
    except Exception as e:
        logger.error(f"Telnet connection error: {e}")
        raise
    finally:
        if tn:
            try:
                tn.write(b"BYE\r\n")
                tn.close()
            except Exception as e:
                logger.warning(f"Error closing telnet connection: {e}")

def sendCommand(input_name: str, output_name: str, audio: bool = True, video: bool = True, max_retries: int = MAX_COMMAND_RETRIES) -> bool:
    """
    Send a routing command to the Crestron switcher.
    
    Args:
        input_name: Input name
        output_name: Output name
        audio: Include audio routing
        video: Include video routing
        max_retries: Maximum number of retry attempts
        
    Returns:
        True if successful, False otherwise
    """
    input_id = getInput(input_name)
    output = getOutput(output_name)
    
    if not output:
        logger.error(f"Output '{output_name}' not found")
        return False
    
    if input_id == "999":
        logger.error(f"Input '{input_name}' not found")
        return False
    
    output_id = output[1]
    cmd = buildCommand(input_id, output_id, audio, video)
    
    for attempt in range(max_retries):
        try:
            with telnet_connection() as tn:
                tn.write(b"\r\n")
                tn.write(b"\r\n")
                tn.write(b"\r\n")
                logger.debug(f"Sending command: {cmd.decode().strip()}")
                tn.write(cmd)
                tn.write(b"\r\n")
            logger.info(f"Successfully sent command: {input_name} -> {output_name}")
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                delay = RETRY_DELAY_BASE * (2 ** attempt)  # Exponential backoff
                logger.warning(f"Command failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)  # This is blocking
            else:
                logger.error(f"Command failed after {max_retries} attempts: {e}")
                return False
    
    return False
# MQTT Functions
def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:  # type: ignore
    """
    Callback for when the client receives a CONNACK response from the server.
    
    Args:
        client: MQTT client instance
        userdata: User data
        flags: Response flags
        reason_code: Connection result code
        properties: Properties
    """
    # Handle both ReasonCode enum and int types
    try:
        rc = int(reason_code.value) if hasattr(reason_code, 'value') else int(reason_code)  # type: ignore
    except (AttributeError, ValueError, TypeError):
        rc = 0
    if rc == 0:
        logger.info("Connected to MQTT Broker!")
        # Subscribe to command topics
        client.subscribe("crestroncontrol/cmd")
        publish_message(client, "crestroncontrol/inputs", json.dumps(getInputs()))
        publish_message(client, "crestroncontrol/outputs", json.dumps(getOutputs()))
        publish_message(client, "crestroncontrol/availability", "online")
        for output in outputs:
            publish_discovery(client, output)
    else:
        logger.error(f"Failed to connect to MQTT broker, return code {rc}")

def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    """
    Callback for when a PUBLISH message is received from the server.
    
    Args:
        client: MQTT client instance
        userdata: User data
        msg: Message object
    """
    publish_message(client, "crestroncontrol/availability", "online")
    topic = msg.topic
    try:
        payload = msg.payload.decode('utf-8')
    except UnicodeDecodeError as e:
        logger.error(f"Failed to decode message payload: {e}")
        return
    
    logger.info(f"Received `{payload}` from `{topic}` topic")

    # Extract output name from topic
    if not topic.startswith('crestroncontrol/cmd/'):
        logger.warning(f"Unexpected topic format: {topic}")
        return
    
    output_name = topic.replace('crestroncontrol/cmd/', '').replace('_', ' ')
    
    # Send command to switcher
    if sendCommand(payload, output_name):
        # Update local state
        output = getOutput(output_name)
        if output:
            # Update the output's current input
            output_index = outputs.index(output)
            outputs[output_index] = (output[0], output[1], getInput(payload))
            publish_state(outputs[output_index])
        else:
            logger.error(f"Output '{output_name}' not found after command")
    else:
        logger.error(f"Failed to send command: {payload} -> {output_name}")

    
def on_disconnect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:  # type: ignore
    """
    Callback for when the client disconnects from the server.
    
    Args:
        client: MQTT client instance
        userdata: User data
        flags: Disconnect flags
        reason_code: Disconnect reason code
        properties: Properties
    """
    # Handle both ReasonCode enum and int types
    try:
        rc = int(reason_code.value) if hasattr(reason_code, 'value') else int(reason_code)  # type: ignore
    except (AttributeError, ValueError, TypeError):
        rc = 0
    logger.info(f"Disconnected from MQTT Broker (reason_code: {rc})")

def publish_state(output: Tuple[str, str, str]) -> None:
    """
    Publish the current state of an output to MQTT.
    
    Args:
        output: Output tuple [name, id, current_input]
    """
    input_name = getInput(output[2])
    topic = f"crestroncontrol/state/{output[0].replace(' ', '_')}"
    publish_message(client, topic, input_name, retain=True)

def publish_discovery(client: mqtt.Client, output: Tuple[str, str, str]) -> None:
    """
    Publish Home Assistant MQTT discovery message for an output.
    
    Args:
        client: MQTT client instance
        output: Output tuple [name, id, current_input]
    """
    name = output[0]
    name_safe = name.replace(' ', '_')
    discovery_payload = {
        "name": f"{name} Input",
        "unique_id": f"Crestron_{name_safe}_Input",
        "command_topic": f"crestroncontrol/cmd/{name_safe}",
        "state_topic": f"crestroncontrol/state/{name_safe}",
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
    discovery_topic = f"homeassistant/select/crestroncontrol/{name_safe}_Input/config"
    publish_message(client, discovery_topic, json.dumps(discovery_payload), retain=True)
    client.subscribe(f"crestroncontrol/cmd/{name_safe}")
    logger.info(f"Published MQTT Discovery message for {name}")
    publish_state(output)

def publish_message(client: mqtt.Client, topic: str, message: str, qos: int = 0, retain: bool = False) -> None:
    """
    Publish a message to an MQTT topic.
    
    Args:
        client: MQTT client instance
        topic: Topic to publish to
        message: Message content
        qos: Quality of Service level (0, 1, or 2)
        retain: Whether to retain the message
    """
    result = client.publish(topic, message, qos, retain)
    status = result[0]
    if status == 0:
        logger.debug(f"Published message to {topic}")
    else:
        logger.error(f"Failed to send message to topic {topic} (status: {status})")

def initialize_switcher() -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """
    Initialize connection to Crestron switcher and parse inputs/outputs.
    
    Returns:
        Tuple of (inputs, outputs) lists
    """
    try:
        with telnet_connection() as tn:
            # Wait for prompt
            tn.read_until(b"DM-MD8x8>", timeout=5)
            tn.write(b"\r\n")
            tn.read_until(b"DM-MD8x8>", timeout=5)
            tn.write(b"\r\n")
            tn.read_until(b"DM-MD8x8>", timeout=5)
            
            # Stop any existing monitors
            tn.write(b"joinmonitorslot stop all\r\n")
            tn.read_until(b"DM-MD8x8>", timeout=5)
            
            # Get card information
            tn.write(b"cards\r\n")
            raw_cards = tn.read_until(b"]\r\n\r\n", timeout=5)
            # Ensure raw_cards is bytes
            if isinstance(raw_cards, str):
                raw_cards = raw_cards.encode('utf-8')
            cards = crestron_parser.parse_cards(raw_cards)
            
            if not cards:
                logger.error("No cards found, cannot initialize switcher")
                return [], []
            
            # Build command to get input/output names
            cmd = b"joingetoutserial 0x04 33"
            input_count, output_count = 0, 0
            for card in cards:
                if card[0] == "Input":
                    input_count += 1
                if card[0] == "Output":
                    output_count += 1
            
            for i in range(input_count):
                cmd += b" " + str(i).encode()
            for i in range(output_count):
                cmd += b" " + str(i + 32).encode()
            cmd += b"\r\n"
            
            tn.write(cmd)
            raw_io = tn.read_until(cmd, timeout=5)
            # Ensure raw_io is bytes
            if isinstance(raw_io, str):
                raw_io = raw_io.encode('utf-8')
            
            inputs_list, outputs_list = crestron_parser.parseIO(cards, raw_io, cmd)
            logger.info(f"Initialized switcher: {len(inputs_list)} inputs, {len(outputs_list)} outputs")
            return inputs_list, outputs_list
            
    except Exception as e:
        logger.error(f"Failed to initialize switcher: {e}")
        raise

# Initialize MQTT client
client = mqtt.Client(CallbackAPIVersion.VERSION2, "crestron2ha")
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

# Set MQTT credentials if provided
if mqtt_username and mqtt_password:
    client.username_pw_set(mqtt_username, mqtt_password)
elif mqtt_username or mqtt_password:
    logger.warning("MQTT username or password provided but not both. Connection may fail.")

try:
    # Connect to MQTT broker
    client.connect(broker, broker_port, MQTT_KEEPALIVE)
    
    # Initialize switcher
    inputs, outputs = initialize_switcher()
    
    if not inputs or not outputs:
        logger.error("Failed to initialize switcher inputs/outputs. Exiting.")
        raise RuntimeError("Switcher initialization failed")
    
    # Start MQTT loop
    client.loop_start()
    
    # Main loop - publish availability periodically
    logger.info("Starting main loop...")
    while True:
        publish_message(client, "crestroncontrol/availability", "online")
        time.sleep(AVAILABILITY_CHECK_INTERVAL)
        
except KeyboardInterrupt:
    logger.info("Received keyboard interrupt, shutting down...")
except Exception as e:
    logger.error(f"Fatal error: {e}", exc_info=True)
finally:
    logger.info("Cleaning up...")
    time.sleep(1)
    publish_message(client, "crestroncontrol/availability", "offline")
    client.disconnect()
    client.loop_stop()
    logger.info("Shutdown complete")
