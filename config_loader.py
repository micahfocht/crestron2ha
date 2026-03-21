"""
Configuration loader for AV routing system.

Handles loading, validating, and auto-generating configuration for
Crestron video switcher and Tesira audio DSP integration.
"""

import json
import os
import logging
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_MQTT_PORT = 1883
DEFAULT_CRESTRON_PORT = 41795
DEFAULT_CONFIG_PATH = "config/av_routing.json"


@dataclass
class MqttConfig:
    """MQTT broker configuration."""
    broker: str = "localhost"
    port: int = DEFAULT_MQTT_PORT
    username: str = ""
    password: str = ""


@dataclass
class CrestronConfig:
    """Crestron switcher configuration."""
    host: str = "localhost"
    port: int = DEFAULT_CRESTRON_PORT


@dataclass
class TesiraUnit:
    """Tesira Forte unit configuration."""
    id: str
    host: str
    ssh_username: str = "default"
    ssh_password: str = "default"
    matrix_instance_tag: str = "MatrixMixer1"


@dataclass
class AudioMapping:
    """Audio channel mapping for Tesira."""
    tesira_unit: str
    channels: list[int]
    channel_labels: list[str] = field(default_factory=list)


@dataclass
class LogicStateMapping:
    """Logic state mapping for Tesira amplifier control."""
    tesira_unit: str
    instance_tag: str
    channel: int = 1


@dataclass
class LevelControlMapping:
    """Level block mapping for Tesira volume control.
    
    Uses a Tesira Level block for volume control instead of
    adjusting matrix mixer crosspoint levels directly.
    When configured, crosspoints are set to 0 dB (reference)
    with mixing offsets, and actual volume is controlled via
    the level block.
    """
    tesira_unit: str
    instance_tag: str
    channels: list[int] = field(default_factory=lambda: [1])


@dataclass
class InputConfig:
    """Input source configuration."""
    name: str
    crestron_id: Optional[int]  # None for audio-only inputs
    audio: Optional[AudioMapping]  # None for HDMI audio only


@dataclass
class OutputConfig:
    """Output zone configuration."""
    name: str
    crestron_id: Optional[int]  # None for audio-only zones
    audio: Optional[AudioMapping]  # None for HDMI audio only
    logic_state: Optional[LogicStateMapping] = None  # Amplifier on/off control
    level_control: Optional[LevelControlMapping] = None  # Level block volume control
    current_input: Optional[str] = None
    volume_db: float = -12.0  # Default -12dB (Tesira max is 0dB)


@dataclass
class AVRoutingConfig:
    """Complete AV routing configuration."""
    mqtt: MqttConfig
    crestron: CrestronConfig
    tesira_units: list[TesiraUnit]
    inputs: list[InputConfig]
    outputs: list[OutputConfig]
    
    def get_tesira_unit(self, unit_id: str) -> Optional[TesiraUnit]:
        """Get a Tesira unit by ID."""
        for unit in self.tesira_units:
            if unit.id == unit_id:
                return unit
        return None
    
    def get_input_by_name(self, name: str) -> Optional[InputConfig]:
        """Get an input by name."""
        for inp in self.inputs:
            if inp.name == name:
                return inp
        return None
    
    def get_input_by_crestron_id(self, crestron_id: int) -> Optional[InputConfig]:
        """Get an input by Crestron ID."""
        for inp in self.inputs:
            if inp.crestron_id == crestron_id:
                return inp
        return None
    
    def get_output_by_name(self, name: str) -> Optional[OutputConfig]:
        """Get an output by name."""
        for out in self.outputs:
            if out.name == name:
                return out
        return None
    
    def get_all_input_names(self) -> list[str]:
        """Get list of all input names."""
        return [inp.name for inp in self.inputs]
    
    def get_input_names_for_output(self, output: 'OutputConfig') -> list[str]:
        """
        Get list of input names valid for a specific output.
        
        For outputs with Tesira audio: all inputs (video + audio-only)
        For outputs without Tesira audio: only video inputs (have crestron_id)
        """
        if output.audio is not None:
            # Output has Tesira audio - show all inputs
            return [inp.name for inp in self.inputs]
        else:
            # Output uses HDMI audio only - show only video inputs
            return [inp.name for inp in self.inputs if inp.crestron_id is not None]
    
    def get_all_output_names(self) -> list[str]:
        """Get list of all output names."""
        return [out.name for out in self.outputs]
    
    def has_tesira_config(self) -> bool:
        """Check if Tesira units are configured."""
        return len(self.tesira_units) > 0


def _parse_audio_mapping(data: Optional[dict]) -> Optional[AudioMapping]:
    """Parse audio mapping from JSON data."""
    if data is None:
        return None
    return AudioMapping(
        tesira_unit=data["tesira_unit"],
        channels=data["channels"],
        channel_labels=data.get("channel_labels", [])
    )


def _parse_logic_state_mapping(data: Optional[dict]) -> Optional[LogicStateMapping]:
    """Parse logic state mapping from JSON data."""
    if data is None:
        return None
    return LogicStateMapping(
        tesira_unit=data["tesira_unit"],
        instance_tag=data["instance_tag"],
        channel=data.get("channel", 1)
    )


def _parse_level_control_mapping(data: Optional[dict]) -> Optional[LevelControlMapping]:
    """Parse level control mapping from JSON data."""
    if data is None:
        return None
    return LevelControlMapping(
        tesira_unit=data["tesira_unit"],
        instance_tag=data["instance_tag"],
        channels=data.get("channels", [1])
    )


def _parse_input(data: dict) -> InputConfig:
    """Parse input configuration from JSON data."""
    return InputConfig(
        name=data["name"],
        crestron_id=data.get("crestron_id"),
        audio=_parse_audio_mapping(data.get("audio"))
    )


def _parse_output(data: dict) -> OutputConfig:
    """Parse output configuration from JSON data."""
    return OutputConfig(
        name=data["name"],
        crestron_id=data.get("crestron_id"),
        audio=_parse_audio_mapping(data.get("audio")),
        logic_state=_parse_logic_state_mapping(data.get("logic_state")),
        level_control=_parse_level_control_mapping(data.get("level_control"))
    )


def _parse_tesira_unit(data: dict) -> TesiraUnit:
    """Parse Tesira unit configuration from JSON data."""
    return TesiraUnit(
        id=data["id"],
        host=data["host"],
        ssh_username=data.get("ssh_username", "default"),
        ssh_password=data.get("ssh_password", "default"),
        matrix_instance_tag=data.get("matrix_instance_tag", "MatrixMixer1")
    )


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Optional[AVRoutingConfig]:
    """
    Load configuration from JSON file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        AVRoutingConfig object or None if file doesn't exist
    """
    if not os.path.exists(config_path):
        logger.info(f"Configuration file not found: {config_path}")
        return None
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return parse_config(data)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        raise
    except KeyError as e:
        logger.error(f"Missing required configuration key: {e}")
        raise
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        raise


def parse_config(data: dict) -> AVRoutingConfig:
    """
    Parse configuration from dictionary.
    
    Args:
        data: Configuration dictionary
        
    Returns:
        AVRoutingConfig object
    """
    # Parse MQTT config
    mqtt_data = data.get("mqtt", {})
    mqtt_config = MqttConfig(
        broker=mqtt_data.get("broker", "localhost"),
        port=mqtt_data.get("port", DEFAULT_MQTT_PORT),
        username=mqtt_data.get("username", ""),
        password=mqtt_data.get("password", "")
    )
    
    # Parse Crestron config
    crestron_data = data.get("crestron", {})
    crestron_config = CrestronConfig(
        host=crestron_data.get("host", "localhost"),
        port=crestron_data.get("port", DEFAULT_CRESTRON_PORT)
    )
    
    # Parse Tesira units
    tesira_units = [
        _parse_tesira_unit(unit_data)
        for unit_data in data.get("tesira_units", [])
    ]
    
    # Parse inputs
    inputs = [
        _parse_input(input_data)
        for input_data in data.get("inputs", [])
    ]
    
    # Ensure "No Input" option exists
    if not any(inp.name == "No Input" for inp in inputs):
        inputs.append(InputConfig(name="No Input", crestron_id=0, audio=None))
    
    # Parse outputs
    outputs = [
        _parse_output(output_data)
        for output_data in data.get("outputs", [])
    ]
    
    return AVRoutingConfig(
        mqtt=mqtt_config,
        crestron=crestron_config,
        tesira_units=tesira_units,
        inputs=inputs,
        outputs=outputs
    )


def generate_config_from_crestron(
    inputs: list[tuple[str, str]],
    outputs: list[tuple[str, str, str]],
    crestron_host: str,
    crestron_port: int = DEFAULT_CRESTRON_PORT,
    mqtt_broker: str = "localhost",
    mqtt_port: int = DEFAULT_MQTT_PORT
) -> dict:
    """
    Generate a configuration dictionary from Crestron discovery.
    
    Args:
        inputs: List of (name, id) tuples from Crestron
        outputs: List of (name, id, current_input) tuples from Crestron
        crestron_host: Crestron switcher IP address
        crestron_port: Crestron telnet port
        mqtt_broker: MQTT broker address
        mqtt_port: MQTT broker port
        
    Returns:
        Configuration dictionary ready to be saved as JSON
    """
    config = {
        "mqtt": {
            "broker": mqtt_broker,
            "port": mqtt_port,
            "username": "",
            "password": ""
        },
        "crestron": {
            "host": crestron_host,
            "port": crestron_port
        },
        "tesira_units": [],
        "inputs": [],
        "outputs": []
    }
    
    # Add discovered inputs (skip "No Input" placeholder)
    for name, crestron_id in inputs:
        if name == "No Input":
            continue
        try:
            config["inputs"].append({
                "name": name,
                "crestron_id": int(crestron_id),
                "audio": None
            })
        except ValueError:
            logger.warning(f"Skipping input with invalid ID: {name} ({crestron_id})")
    
    # Add discovered outputs
    for name, crestron_id, _ in outputs:
        try:
            config["outputs"].append({
                "name": name,
                "crestron_id": int(crestron_id),
                "audio": None
            })
        except ValueError:
            logger.warning(f"Skipping output with invalid ID: {name} ({crestron_id})")
    
    return config


def save_config(config: dict, config_path: str = DEFAULT_CONFIG_PATH) -> None:
    """
    Save configuration to JSON file.
    
    Args:
        config: Configuration dictionary
        config_path: Path to save the configuration file
    """
    # Ensure directory exists
    config_dir = os.path.dirname(config_path)
    if config_dir and not os.path.exists(config_dir):
        os.makedirs(config_dir)
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    
    logger.info(f"Configuration saved to: {config_path}")


def validate_config(config: AVRoutingConfig) -> list[str]:
    """
    Validate configuration and return list of warnings/issues.
    
    Args:
        config: Configuration to validate
        
    Returns:
        List of warning messages
    """
    warnings = []
    
    # Check for empty inputs/outputs
    if not config.inputs:
        warnings.append("No inputs configured")
    if not config.outputs:
        warnings.append("No outputs configured")
    
    # Check Tesira configuration
    if not config.tesira_units:
        warnings.append("No Tesira units configured - audio routing will be disabled")
    
    # Validate audio mappings reference valid Tesira units
    tesira_unit_ids = {unit.id for unit in config.tesira_units}
    
    for inp in config.inputs:
        if inp.audio and inp.audio.tesira_unit not in tesira_unit_ids:
            warnings.append(
                f"Input '{inp.name}' references unknown Tesira unit: {inp.audio.tesira_unit}"
            )
    
    for out in config.outputs:
        if out.audio and out.audio.tesira_unit not in tesira_unit_ids:
            warnings.append(
                f"Output '{out.name}' references unknown Tesira unit: {out.audio.tesira_unit}"
            )
        if out.logic_state and out.logic_state.tesira_unit not in tesira_unit_ids:
            warnings.append(
                f"Output '{out.name}' logic state references unknown Tesira unit: {out.logic_state.tesira_unit}"
            )
        if out.level_control and out.level_control.tesira_unit not in tesira_unit_ids:
            warnings.append(
                f"Output '{out.name}' level control references unknown Tesira unit: {out.level_control.tesira_unit}"
            )
    
    # Check for channel label mismatches
    for inp in config.inputs:
        if inp.audio and inp.audio.channel_labels:
            if len(inp.audio.channels) != len(inp.audio.channel_labels):
                warnings.append(
                    f"Input '{inp.name}' has mismatched channel count and labels"
                )
    
    for out in config.outputs:
        if out.audio and out.audio.channel_labels:
            if len(out.audio.channels) != len(out.audio.channel_labels):
                warnings.append(
                    f"Output '{out.name}' has mismatched channel count and labels"
                )
    
    return warnings
