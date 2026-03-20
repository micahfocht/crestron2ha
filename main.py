"""
Crestron Matrix Switcher + Tesira Audio DSP to Home Assistant MQTT Bridge.

This script connects to a Crestron Matrix Switcher via Telnet and Tesira Forte
units via SSH, publishing their state to Home Assistant via MQTT.
It listens for commands to change video/audio routing and zone volumes.
"""

from Exscript.protocols import telnetlib
import time
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion  # type: ignore
import json
import os
import sys
import logging
from typing import Optional, Any
from contextlib import contextmanager

import crestron_parser
import config_loader
from config_loader import (
    AVRoutingConfig, InputConfig, OutputConfig,
    load_config, generate_config_from_crestron, save_config, validate_config,
    DEFAULT_CONFIG_PATH
)
from tesira_controller import TesiraController, PARAMIKO_AVAILABLE

# Constants
MQTT_KEEPALIVE = 60
AVAILABILITY_CHECK_INTERVAL = 10
MAX_COMMAND_RETRIES = 3
RETRY_DELAY_BASE = 1.0

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Determine config path based on environment
if os.path.exists('/.dockerenv'):
    CONFIG_PATH = '/config/av_routing.json'
else:
    CONFIG_PATH = 'config/av_routing.json'

# Global state
config: Optional[AVRoutingConfig] = None
tesira: Optional[TesiraController] = None
client: Optional[mqtt.Client] = None


@contextmanager
def telnet_connection(host: str, port: int):
    """
    Context manager for telnet connections to ensure proper cleanup.
    
    Args:
        host: Crestron switcher hostname/IP
        port: Telnet port
        
    Yields:
        Telnet connection object
    """
    tn = None
    try:
        tn = telnetlib.Telnet(host, port, timeout=5)
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


def get_input_by_name_or_id(name: str) -> Optional[InputConfig]:
    """Get input by name or Crestron ID."""
    if config is None:
        return None
    
    # Try by name first
    inp = config.get_input_by_name(name)
    if inp:
        return inp
    
    # Try by Crestron ID
    try:
        crestron_id = int(name)
        return config.get_input_by_crestron_id(crestron_id)
    except ValueError:
        return None


def build_crestron_command(input_id: int, output_id: int, video_only: bool = False) -> bytes:
    """
    Build a Crestron routing command.
    
    Args:
        input_id: Input Crestron ID
        output_id: Output Crestron ID
        video_only: If True, route video only (audio handled by Tesira)
        
    Returns:
        Encoded command bytes
    """
    if video_only:
        cmd = f"SETVIDEOROUTE {input_id} {output_id}\r\n"
    else:
        cmd = f"SETAVROUTE {input_id} {output_id}\r\n"
    return cmd.encode()


def send_crestron_command(
    input_config: InputConfig,
    output_config: OutputConfig,
    max_retries: int = MAX_COMMAND_RETRIES
) -> bool:
    """
    Send a routing command to the Crestron switcher.
    
    Args:
        input_config: Input configuration
        output_config: Output configuration
        max_retries: Maximum retry attempts
        
    Returns:
        True if successful, False otherwise
    """
    if config is None:
        return False
    
    # Check if both have Crestron IDs
    if input_config.crestron_id is None or output_config.crestron_id is None:
        logger.debug("Skipping Crestron command - no Crestron ID on input or output")
        return True
    
    # Use video-only if output has Tesira audio
    video_only = output_config.audio is not None
    cmd = build_crestron_command(
        input_config.crestron_id,
        output_config.crestron_id,
        video_only
    )
    
    for attempt in range(max_retries):
        try:
            with telnet_connection(config.crestron.host, config.crestron.port) as tn:
                tn.write(b"\r\n")
                tn.write(b"\r\n")
                tn.write(b"\r\n")
                logger.debug(f"Sending command: {cmd.decode().strip()}")
                tn.write(cmd)
                tn.write(b"\r\n")
            logger.info(f"Crestron: {input_config.name} -> {output_config.name}")
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(f"Command failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"Crestron command failed after {max_retries} attempts: {e}")
                return False
    
    return False


def send_tesira_routing(
    input_config: Optional[InputConfig],
    output_config: OutputConfig,
    previous_input_config: Optional[InputConfig] = None
) -> bool:
    """
    Send audio routing commands to Tesira.
    
    Args:
        input_config: Input configuration (None to clear routing)
        output_config: Output configuration
        previous_input_config: Previous input for faster crosspoint clearing
        
    Returns:
        True if successful, False otherwise
    """
    if tesira is None:
        logger.debug("Tesira controller not available")
        return True
    
    if output_config.audio is None:
        logger.debug(f"Output '{output_config.name}' has no Tesira audio config")
        return True
    
    input_audio = input_config.audio if input_config else None
    previous_audio = previous_input_config.audio if previous_input_config else None
    
    return tesira.route_audio(
        input_audio,
        output_config.audio,
        output_config.volume_db,
        previous_audio
    )


def send_tesira_logic_state(output_config: OutputConfig, state: bool) -> bool:
    """
    Set the Tesira logic state for an output's amplifier.
    
    Args:
        output_config: Output configuration with optional logic_state mapping
        state: True to turn amplifier on, False to turn off
        
    Returns:
        True if successful or not applicable, False on error
    """
    if tesira is None:
        logger.debug("Tesira controller not available")
        return True
    
    if output_config.logic_state is None:
        return True
    
    return tesira.set_logic_state_from_mapping(output_config.logic_state, state)


def route_source(input_name: str, output_name: str) -> bool:
    """
    Route a source to an output (video + audio).
    
    Args:
        input_name: Input source name
        output_name: Output/zone name
        
    Returns:
        True if successful, False otherwise
    """
    if config is None:
        logger.error("Configuration not loaded")
        return False
    
    # Look up input and output
    input_config = get_input_by_name_or_id(input_name)
    output_config = config.get_output_by_name(output_name)
    
    if output_config is None:
        logger.error(f"Output '{output_name}' not found")
        return False
    
    # Get previous input for faster Tesira switching
    previous_input_config = None
    if output_config.current_input:
        previous_input_config = config.get_input_by_name(output_config.current_input)
    
    # Handle "No Input" case
    if input_name == "No Input" or input_config is None:
        if input_name != "No Input":
            logger.error(f"Input '{input_name}' not found")
            return False
        input_config = config.get_input_by_name("No Input")
    
    # Route video via Crestron
    crestron_success = True
    if output_config.crestron_id is not None:
        if input_config and input_config.crestron_id is not None:
            # Normal video input
            crestron_success = send_crestron_command(input_config, output_config)
        else:
            # Audio-only input or No Input - route video to input 0 (no signal)
            no_input = config.get_input_by_name("No Input")
            if no_input:
                crestron_success = send_crestron_command(no_input, output_config)
    
    # Route audio via Tesira (if applicable)
    tesira_success = send_tesira_routing(input_config, output_config, previous_input_config)
    
    # Set amplifier logic state (on when audio input is active, off for "No Input")
    has_audio_input = (
        input_config is not None and
        input_config.name != "No Input" and
        input_config.audio is not None
    )
    logic_state_success = send_tesira_logic_state(output_config, has_audio_input)
    
    # Update output state
    if crestron_success and tesira_success and logic_state_success:
        output_config.current_input = input_name
        return True
    
    return False


def set_zone_volume(output_name: str, volume_db: float) -> bool:
    """
    Set the volume for a zone.
    
    Args:
        output_name: Output/zone name
        volume_db: Volume in dB
        
    Returns:
        True if successful, False otherwise
    """
    if config is None or tesira is None:
        return False
    
    output_config = config.get_output_by_name(output_name)
    if output_config is None:
        logger.error(f"Output '{output_name}' not found")
        return False
    
    if output_config.audio is None:
        logger.debug(f"Output '{output_name}' has no Tesira audio")
        return True
    
    # Get current input for this output
    input_config = None
    if output_config.current_input:
        input_config = config.get_input_by_name(output_config.current_input)
    
    if input_config is None or input_config.audio is None:
        logger.debug(f"No audio input routed to '{output_name}'")
        return True
    
    # Update stored volume and send to Tesira
    output_config.volume_db = volume_db
    return tesira.set_zone_volume(input_config.audio, output_config.audio, volume_db)


# MQTT Callbacks
def on_connect(mqtt_client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
    """Handle MQTT connection."""
    try:
        rc = int(reason_code.value) if hasattr(reason_code, 'value') else int(reason_code)
    except (AttributeError, ValueError, TypeError):
        rc = 0
    
    if rc == 0:
        logger.info("Connected to MQTT Broker!")
        mqtt_client.subscribe("crestroncontrol/cmd")
        
        if config:
            publish_message(mqtt_client, "crestroncontrol/inputs", json.dumps(config.get_all_input_names()))
            publish_message(mqtt_client, "crestroncontrol/outputs", json.dumps(config.get_all_output_names()))
            publish_message(mqtt_client, "crestroncontrol/availability", "online")
            
            for output in config.outputs:
                publish_discovery(mqtt_client, output)
    else:
        logger.error(f"Failed to connect to MQTT broker, return code {rc}")


def on_message(mqtt_client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    """Handle incoming MQTT messages."""
    publish_message(mqtt_client, "crestroncontrol/availability", "online")
    
    topic = msg.topic
    try:
        payload = msg.payload.decode('utf-8')
    except UnicodeDecodeError as e:
        logger.error(f"Failed to decode message payload: {e}")
        return
    
    logger.info(f"Received `{payload}` from `{topic}` topic")
    
    # Handle source selection commands
    if topic.startswith('crestroncontrol/cmd/'):
        output_name = topic.replace('crestroncontrol/cmd/', '').replace('_', ' ')
        
        if route_source(payload, output_name):
            output = config.get_output_by_name(output_name) if config else None
            if output:
                publish_state(mqtt_client, output)
        else:
            logger.error(f"Failed to route: {payload} -> {output_name}")
    
    # Handle volume commands
    elif topic.startswith('crestroncontrol/volume/'):
        output_name = topic.replace('crestroncontrol/volume/', '').replace('_', ' ')
        try:
            volume_db = float(payload)
            if set_zone_volume(output_name, volume_db):
                publish_volume_state(mqtt_client, output_name, volume_db)
            else:
                logger.error(f"Failed to set volume for {output_name}")
        except ValueError:
            logger.error(f"Invalid volume value: {payload}")
    
    # Handle amplifier logic state commands
    elif topic.startswith('crestroncontrol/amp/'):
        output_name = topic.replace('crestroncontrol/amp/', '').replace('_', ' ')
        output = config.get_output_by_name(output_name) if config else None
        if output and output.logic_state is not None:
            state = payload.upper() == "ON"
            if send_tesira_logic_state(output, state):
                publish_amp_state(mqtt_client, output_name, state)
            else:
                logger.error(f"Failed to set amplifier state for {output_name}")
        else:
            logger.error(f"No logic state configured for output '{output_name}'")


def on_disconnect(mqtt_client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
    """Handle MQTT disconnection."""
    try:
        rc = int(reason_code.value) if hasattr(reason_code, 'value') else int(reason_code)
    except (AttributeError, ValueError, TypeError):
        rc = 0
    logger.info(f"Disconnected from MQTT Broker (reason_code: {rc})")


def publish_state(mqtt_client: mqtt.Client, output: OutputConfig) -> None:
    """Publish the current state of an output."""
    topic = f"crestroncontrol/state/{output.name.replace(' ', '_')}"
    state = output.current_input or "No Input"
    publish_message(mqtt_client, topic, state, retain=True)
    
    # Publish amplifier state if logic state is configured
    if output.logic_state is not None:
        has_audio_input = (
            output.current_input is not None and
            output.current_input != "No Input"
        )
        # Check if the current input actually has audio
        if has_audio_input and config is not None:
            input_config = config.get_input_by_name(output.current_input)
            has_audio_input = input_config is not None and input_config.audio is not None
        publish_amp_state(mqtt_client, output.name, has_audio_input)


def publish_volume_state(mqtt_client: mqtt.Client, output_name: str, volume_db: float) -> None:
    """Publish the current volume state of an output."""
    topic = f"crestroncontrol/volume_state/{output_name.replace(' ', '_')}"
    publish_message(mqtt_client, topic, str(volume_db), retain=True)


def publish_amp_state(mqtt_client: mqtt.Client, output_name: str, state: bool) -> None:
    """Publish the current amplifier logic state of an output."""
    topic = f"crestroncontrol/amp_state/{output_name.replace(' ', '_')}"
    publish_message(mqtt_client, topic, "ON" if state else "OFF", retain=True)


def publish_discovery(mqtt_client: mqtt.Client, output: OutputConfig) -> None:
    """Publish Home Assistant MQTT discovery messages for an output."""
    if config is None:
        return
    
    name = output.name
    name_safe = name.replace(' ', '_')
    
    # Source selector entity
    # Only show audio-only inputs for outputs that have Tesira audio
    available_inputs = config.get_input_names_for_output(output)
    
    discovery_payload = {
        "name": f"{name} Input",
        "unique_id": f"AVControl_{name_safe}_Input",
        "command_topic": f"crestroncontrol/cmd/{name_safe}",
        "state_topic": f"crestroncontrol/state/{name_safe}",
        "options": available_inputs,
        "availability_topic": "crestroncontrol/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "manufacturer": "Crestron/Biamp",
            "model": "AV Matrix System",
            "name": "AV Control",
            "identifiers": ["av_control_system"],
            "sw_version": "2.0.0"
        },
    }
    
    discovery_topic = f"homeassistant/select/crestroncontrol/{name_safe}_Input/config"
    publish_message(mqtt_client, discovery_topic, json.dumps(discovery_payload), retain=True)
    mqtt_client.subscribe(f"crestroncontrol/cmd/{name_safe}")
    
    # Volume control entity (if output has Tesira audio)
    if output.audio is not None:
        volume_payload = {
            "name": f"{name} Volume",
            "unique_id": f"AVControl_{name_safe}_Volume",
            "command_topic": f"crestroncontrol/volume/{name_safe}",
            "state_topic": f"crestroncontrol/volume_state/{name_safe}",
            "min": -100,
            "max": 0,
            "step": 0.5,
            "unit_of_measurement": "dB",
            "availability_topic": "crestroncontrol/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "manufacturer": "Crestron/Biamp",
                "model": "AV Matrix System",
                "name": "AV Control",
                "identifiers": ["av_control_system"]
            },
        }
        
        volume_discovery_topic = f"homeassistant/number/crestroncontrol/{name_safe}_Volume/config"
        publish_message(mqtt_client, volume_discovery_topic, json.dumps(volume_payload), retain=True)
        mqtt_client.subscribe(f"crestroncontrol/volume/{name_safe}")
        publish_volume_state(mqtt_client, name, output.volume_db)
    
    # Amplifier logic state entity (if output has logic state configured)
    if output.logic_state is not None:
        amp_payload = {
            "name": f"{name} Amplifier",
            "unique_id": f"AVControl_{name_safe}_Amplifier",
            "command_topic": f"crestroncontrol/amp/{name_safe}",
            "state_topic": f"crestroncontrol/amp_state/{name_safe}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": "crestroncontrol/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "manufacturer": "Crestron/Biamp",
                "model": "AV Matrix System",
                "name": "AV Control",
                "identifiers": ["av_control_system"]
            },
        }
        
        amp_discovery_topic = f"homeassistant/switch/crestroncontrol/{name_safe}_Amplifier/config"
        publish_message(mqtt_client, amp_discovery_topic, json.dumps(amp_payload), retain=True)
        mqtt_client.subscribe(f"crestroncontrol/amp/{name_safe}")
        # Publish initial state as OFF
        publish_amp_state(mqtt_client, name, False)
    
    logger.info(f"Published MQTT Discovery for {name}")
    publish_state(mqtt_client, output)


def publish_message(mqtt_client: mqtt.Client, topic: str, message: str, qos: int = 0, retain: bool = False) -> None:
    """Publish a message to an MQTT topic."""
    result = mqtt_client.publish(topic, message, qos, retain)
    status = result[0]
    if status == 0:
        logger.debug(f"Published message to {topic}")
    else:
        logger.error(f"Failed to send message to topic {topic} (status: {status})")


def initialize_crestron(host: str, port: int):
    """
    Initialize connection to Crestron switcher and discover inputs/outputs.
    
    Returns:
        Tuple of (inputs, outputs) lists
    """
    try:
        with telnet_connection(host, port) as tn:
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
            if isinstance(raw_io, str):
                raw_io = raw_io.encode('utf-8')
            
            inputs_list, outputs_list = crestron_parser.parseIO(cards, raw_io, cmd)
            logger.info(f"Discovered from Crestron: {len(inputs_list)} inputs, {len(outputs_list)} outputs")
            return inputs_list, outputs_list
            
    except Exception as e:
        logger.error(f"Failed to initialize switcher: {e}")
        raise


def generate_initial_config(crestron_host: str, crestron_port: int) -> None:
    """Generate initial config from Crestron discovery and exit."""
    logger.info("No configuration file found. Generating from Crestron discovery...")
    
    try:
        inputs, outputs = initialize_crestron(crestron_host, crestron_port)
        
        if not inputs or not outputs:
            logger.error("Failed to discover Crestron inputs/outputs")
            sys.exit(1)
        
        config_dict = generate_config_from_crestron(
            inputs, outputs,
            crestron_host, crestron_port
        )
        
        save_config(config_dict, CONFIG_PATH)
        
        logger.info("=" * 60)
        logger.info("Configuration file generated successfully!")
        logger.info(f"Location: {CONFIG_PATH}")
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Edit the configuration file to add your Tesira units")
        logger.info("2. Configure audio channel mappings for inputs/outputs")
        logger.info("3. Set your MQTT broker credentials")
        logger.info("4. Restart the application")
        logger.info("=" * 60)
        
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Failed to generate configuration: {e}")
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    global config, tesira, client
    
    # Try to load configuration
    config = load_config(CONFIG_PATH)
    
    if config is None:
        # No config file - need to generate one
        # Check for environment variables for initial Crestron connection
        crestron_host = os.environ.get('CRESTRON_HOST', os.environ.get('SWITCHER_IP', ''))
        crestron_port = int(os.environ.get('CRESTRON_PORT', '41795'))
        
        if not crestron_host:
            logger.error("No configuration file found and CRESTRON_HOST not set.")
            logger.error(f"Please create {CONFIG_PATH} or set CRESTRON_HOST environment variable.")
            sys.exit(1)
        
        generate_initial_config(crestron_host, crestron_port)
        return
    
    # Validate configuration
    warnings = validate_config(config)
    for warning in warnings:
        logger.warning(f"Config: {warning}")
    
    # Initialize Tesira controller if units are configured
    if config.tesira_units and PARAMIKO_AVAILABLE:
        tesira = TesiraController(config.tesira_units)
        connection_results = tesira.connect_all()
        for unit_id, success in connection_results.items():
            if success:
                logger.info(f"Connected to Tesira unit: {unit_id}")
            else:
                logger.warning(f"Failed to connect to Tesira unit: {unit_id}")
    elif config.tesira_units and not PARAMIKO_AVAILABLE:
        logger.warning("Tesira units configured but paramiko not installed. Audio routing disabled.")
    else:
        logger.info("No Tesira units configured - running in video-only mode")
    
    # Initialize MQTT client
    client = mqtt.Client(CallbackAPIVersion.VERSION2, "avcontrol2ha")
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    
    # Set MQTT credentials if provided
    if config.mqtt.username and config.mqtt.password:
        client.username_pw_set(config.mqtt.username, config.mqtt.password)
    elif config.mqtt.username or config.mqtt.password:
        logger.warning("MQTT username or password provided but not both.")
    
    try:
        # Connect to MQTT broker
        logger.info(f"Connecting to MQTT broker at {config.mqtt.broker}:{config.mqtt.port}")
        client.connect(config.mqtt.broker, config.mqtt.port, MQTT_KEEPALIVE)
        
        # Verify Crestron connection by doing a discovery
        # (This also syncs any name changes from the config)
        logger.info(f"Connecting to Crestron at {config.crestron.host}:{config.crestron.port}")
        crestron_inputs, crestron_outputs = initialize_crestron(
            config.crestron.host, config.crestron.port
        )
        
        if not crestron_inputs or not crestron_outputs:
            logger.error("Failed to connect to Crestron switcher")
            raise RuntimeError("Crestron initialization failed")
        
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
        
        if client:
            publish_message(client, "crestroncontrol/availability", "offline")
            time.sleep(0.5)
            client.disconnect()
            client.loop_stop()
        
        if tesira:
            tesira.disconnect_all()
        
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
