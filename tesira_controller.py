"""
Tesira Forte DSP controller module.

Handles SSH/TTP communication with Biamp Tesira Forte units
for matrix mixer crosspoint control and zone volume management.
"""

import logging
import time
from typing import Optional
from dataclasses import dataclass
from contextlib import contextmanager

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    paramiko = None  # type: ignore

from config_loader import TesiraUnit, AudioMapping

logger = logging.getLogger(__name__)

# Constants
SSH_TIMEOUT = 10
COMMAND_DELAY = 0.01  # Reduced delay between commands for faster switching
MAX_RETRIES = 3
RETRY_DELAY = 1.0

# Default and limits for crosspoint levels
DEFAULT_LEVEL_DB = -12.0  # Default crosspoint level
MAX_LEVEL_DB = 0.0  # Tesira matrix mixer max is 0dB
MIN_LEVEL_DB = -100.0

# Stereo to surround mixing levels (in dB, relative to main level)
STEREO_TO_CENTER_OFFSET = -15.0  # L+R mixed to Center
STEREO_TO_SURROUND_OFFSET = -20.0  # L/R to SL/SR


@dataclass
class CrosspointState:
    """State of a matrix mixer crosspoint."""
    input_channel: int
    output_channel: int
    enabled: bool
    level_db: float = 0.0


class TesiraConnection:
    """
    Manages SSH connection to a Tesira Forte unit.
    
    Uses Tesira Text Protocol (TTP) over SSH for control.
    """
    
    def __init__(self, unit: TesiraUnit):
        """
        Initialize Tesira connection.
        
        Args:
            unit: TesiraUnit configuration
        """
        self.unit = unit
        self._client: Optional[paramiko.SSHClient] = None
        self._channel: Optional[paramiko.Channel] = None
    
    def connect(self) -> bool:
        """
        Establish SSH connection to the Tesira unit.
        
        Returns:
            True if connection successful, False otherwise
        """
        if not PARAMIKO_AVAILABLE:
            logger.error("paramiko not installed - cannot connect to Tesira")
            return False
        
        try:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            logger.info(f"Connecting to Tesira unit '{self.unit.id}' at {self.unit.host}")
            self._client.connect(
                hostname=self.unit.host,
                username=self.unit.ssh_username,
                password=self.unit.ssh_password,
                timeout=SSH_TIMEOUT,
                look_for_keys=False,
                allow_agent=False
            )
            
            # Open interactive shell for TTP commands
            self._channel = self._client.invoke_shell()
            self._channel.settimeout(SSH_TIMEOUT)
            
            # Read initial prompt/banner
            time.sleep(0.5)
            if self._channel.recv_ready():
                self._channel.recv(4096)
            
            logger.info(f"Connected to Tesira unit '{self.unit.id}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Tesira unit '{self.unit.id}': {e}")
            self.disconnect()
            return False
    
    def disconnect(self) -> None:
        """Close the SSH connection."""
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
    
    def is_connected(self) -> bool:
        """Check if connection is active."""
        if not self._client or not self._channel:
            return False
        try:
            transport = self._client.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False
    
    def send_command(self, command: str, wait_for_response: bool = True) -> tuple[bool, str]:
        """
        Send a TTP command to the Tesira unit.
        
        Args:
            command: TTP command string (without newline)
            wait_for_response: If False, don't wait for response (faster batching)
            
        Returns:
            Tuple of (success, response)
        """
        if not self.is_connected():
            if not self.connect():
                return False, "Connection failed"
        
        try:
            # Send command
            cmd = f"{command}\n"
            self._channel.send(cmd.encode())  # type: ignore
            
            if not wait_for_response:
                return True, ""
            
            # Wait for response
            time.sleep(COMMAND_DELAY)
            
            response = ""
            while self._channel.recv_ready():  # type: ignore
                chunk = self._channel.recv(4096).decode('utf-8', errors='replace')  # type: ignore
                response += chunk
                time.sleep(0.005)
            
            # Check for error response
            if "+OK" in response:
                return True, response
            elif "-ERR" in response:
                logger.warning(f"Tesira command error: {command} -> {response.strip()}")
                return False, response
            else:
                # No explicit OK/ERR, assume success
                return True, response
                
        except Exception as e:
            logger.error(f"Error sending command to Tesira: {e}")
            return False, str(e)
    
    def send_commands_batch(self, commands: list[str]) -> bool:
        """
        Send multiple TTP commands in a single write for synchronized execution.
        
        Concatenates all commands into one buffer and sends in a single
        socket write to minimize timing differences between commands.
        
        Args:
            commands: List of TTP command strings
            
        Returns:
            True if all commands sent successfully
        """
        if not commands:
            return True
            
        if not self.is_connected():
            if not self.connect():
                return False
        
        try:
            # Concatenate all commands into a single buffer
            # This sends them in one TCP packet for synchronized execution
            batch = "\n".join(commands) + "\n"
            self._channel.sendall(batch.encode())  # type: ignore
            
            # Small delay then drain any responses
            time.sleep(COMMAND_DELAY)
            while self._channel.recv_ready():  # type: ignore
                self._channel.recv(4096)  # type: ignore
            
            logger.debug(f"Sent {len(commands)} commands in single batch")
            return True
        except Exception as e:
            logger.error(f"Error sending batch commands to Tesira: {e}")
            return False


class TesiraController:
    """
    Controller for managing multiple Tesira Forte units.
    
    Provides high-level methods for audio routing and volume control.
    """
    
    def __init__(self, units: list[TesiraUnit]):
        """
        Initialize controller with Tesira units.
        
        Args:
            units: List of TesiraUnit configurations
        """
        self._connections: dict[str, TesiraConnection] = {}
        
        for unit in units:
            self._connections[unit.id] = TesiraConnection(unit)
        
        logger.info(f"TesiraController initialized with {len(units)} units")
    
    def connect_all(self) -> dict[str, bool]:
        """
        Connect to all configured Tesira units.
        
        Returns:
            Dictionary mapping unit IDs to connection success status
        """
        results = {}
        for unit_id, connection in self._connections.items():
            results[unit_id] = connection.connect()
        return results
    
    def disconnect_all(self) -> None:
        """Disconnect from all Tesira units."""
        for connection in self._connections.values():
            connection.disconnect()
    
    def get_connection(self, unit_id: str) -> Optional[TesiraConnection]:
        """Get connection for a specific unit."""
        return self._connections.get(unit_id)
    
    def set_crosspoint_state(
        self,
        unit_id: str,
        instance_tag: str,
        input_channel: int,
        output_channel: int,
        enabled: bool
    ) -> bool:
        """
        Set the state (on/off) of a matrix mixer crosspoint.
        
        Args:
            unit_id: Tesira unit ID
            instance_tag: Matrix mixer instance tag
            input_channel: Input channel number (1-based)
            output_channel: Output channel number (1-based)
            enabled: True to enable crosspoint, False to disable
            
        Returns:
            True if successful, False otherwise
        """
        connection = self.get_connection(unit_id)
        if not connection:
            logger.error(f"Unknown Tesira unit: {unit_id}")
            return False
        
        state_str = "true" if enabled else "false"
        command = f"{instance_tag} set crosspointLevelState {input_channel} {output_channel} {state_str}"
        
        success, _ = connection.send_command(command)
        if success:
            logger.debug(f"Set crosspoint {input_channel}->{output_channel} = {enabled}")
        return success
    
    def set_crosspoint_level(
        self,
        unit_id: str,
        instance_tag: str,
        input_channel: int,
        output_channel: int,
        level_db: float
    ) -> bool:
        """
        Set the level of a matrix mixer crosspoint.
        
        Args:
            unit_id: Tesira unit ID
            instance_tag: Matrix mixer instance tag
            input_channel: Input channel number (1-based)
            output_channel: Output channel number (1-based)
            level_db: Level in dB (typically -100.0 to +12.0)
            
        Returns:
            True if successful, False otherwise
        """
        connection = self.get_connection(unit_id)
        if not connection:
            logger.error(f"Unknown Tesira unit: {unit_id}")
            return False
        
        # Clamp level to valid range (Tesira max is 0dB)
        level_db = max(MIN_LEVEL_DB, min(MAX_LEVEL_DB, level_db))
        
        command = f"{instance_tag} set crosspointLevel {input_channel} {output_channel} {level_db:.1f}"
        
        success, _ = connection.send_command(command)
        if success:
            logger.debug(f"Set crosspoint level {input_channel}->{output_channel} = {level_db:.1f}dB")
        return success
    
    def clear_output_crosspoints(
        self,
        unit_id: str,
        instance_tag: str,
        output_channels: list[int],
        total_inputs: int = 32
    ) -> bool:
        """
        Disable all crosspoints for specified output channels.
        
        Args:
            unit_id: Tesira unit ID
            instance_tag: Matrix mixer instance tag
            output_channels: List of output channel numbers to clear
            total_inputs: Total number of input channels in the matrix
            
        Returns:
            True if all successful, False if any failed
        """
        connection = self.get_connection(unit_id)
        if not connection:
            logger.error(f"Unknown Tesira unit: {unit_id}")
            return False
        
        all_success = True
        for output_ch in output_channels:
            for input_ch in range(1, total_inputs + 1):
                success = self.set_crosspoint_state(
                    unit_id, instance_tag, input_ch, output_ch, False
                )
                if not success:
                    all_success = False
        
        return all_success
    
    def route_audio(
        self,
        input_audio: Optional[AudioMapping],
        output_audio: Optional[AudioMapping],
        level_db: float = DEFAULT_LEVEL_DB,
        previous_input_audio: Optional[AudioMapping] = None
    ) -> bool:
        """
        Route audio from input to output with intelligent channel mapping.
        
        Maps channels based on labels when available, otherwise maps by position.
        For surround sound, matches FL->FL, FR->FR, etc.
        For stereo to surround, creates downmix with appropriate levels.
        
        Uses batch commands for faster switching.
        
        Args:
            input_audio: Input audio mapping (None to clear output)
            output_audio: Output audio mapping
            level_db: Base crosspoint level in dB
            previous_input_audio: Previous input to clear (optimization)
            
        Returns:
            True if successful, False otherwise
        """
        if output_audio is None:
            logger.debug("Output has no Tesira audio config, skipping audio routing")
            return True
        
        connection = self.get_connection(output_audio.tesira_unit)
        if not connection:
            logger.error(f"Unknown Tesira unit: {output_audio.tesira_unit}")
            return False
        
        unit = connection.unit
        commands: list[str] = []
        
        # Build commands to clear previous routing
        if previous_input_audio is not None:
            old_channel_map = self._build_channel_map(previous_input_audio, output_audio, 0.0)
            for in_ch, out_ch, _ in old_channel_map:
                commands.append(
                    f"{unit.matrix_instance_tag} set crosspointLevelState {in_ch} {out_ch} false"
                )
        
        # If no input, just send clear commands
        if input_audio is None:
            if commands:
                connection.send_commands_batch(commands)
            logger.debug(f"Cleared audio routing for output channels: {output_audio.channels}")
            return True
        
        # Build new channel mapping with levels
        channel_map = self._build_channel_map(input_audio, output_audio, level_db)
        
        # Add commands to enable and set levels for new crosspoints
        for in_ch, out_ch, ch_level in channel_map:
            # Clamp level to valid range
            ch_level = max(MIN_LEVEL_DB, min(MAX_LEVEL_DB, ch_level))
            commands.append(
                f"{unit.matrix_instance_tag} set crosspointLevelState {in_ch} {out_ch} true"
            )
            commands.append(
                f"{unit.matrix_instance_tag} set crosspointLevel {in_ch} {out_ch} {ch_level:.1f}"
            )
        
        # Send all commands as a batch
        success = connection.send_commands_batch(commands)
        
        if success:
            logger.info(
                f"Routed audio: {len(channel_map)} crosspoints at base {level_db:.1f}dB"
            )
        
        return success
    
    def _build_channel_map(
        self,
        input_audio: AudioMapping,
        output_audio: AudioMapping,
        base_level_db: float = 0.0
    ) -> list[tuple[int, int, float]]:
        """
        Build a list of (input_channel, output_channel, level_db) tuples for routing.
        
        Uses channel labels for intelligent mapping when available.
        For stereo to surround, creates appropriate downmix with level offsets:
        - L -> FL, R -> FR at base level
        - L+R -> C at base level - 15dB
        - L -> SL, R -> SR at base level - 20dB
        
        Returns:
            List of (input_ch, output_ch, level_db) tuples
        """
        channel_map: list[tuple[int, int, float]] = []
        
        # If both have labels, match by label with stereo-to-surround support
        if input_audio.channel_labels and output_audio.channel_labels:
            input_label_to_ch = {
                label: ch for ch, label in 
                zip(input_audio.channels, input_audio.channel_labels)
            }
            
            # Check if input is stereo (L/R only)
            is_stereo_input = set(input_audio.channel_labels) == {"L", "R"}
            
            for out_ch, out_label in zip(output_audio.channels, output_audio.channel_labels):
                # Try exact match first
                if out_label in input_label_to_ch:
                    channel_map.append((input_label_to_ch[out_label], out_ch, base_level_db))
                # Handle stereo to surround mapping
                elif is_stereo_input:
                    if out_label == "FL" and "L" in input_label_to_ch:
                        channel_map.append((input_label_to_ch["L"], out_ch, base_level_db))
                    elif out_label == "FR" and "R" in input_label_to_ch:
                        channel_map.append((input_label_to_ch["R"], out_ch, base_level_db))
                    elif out_label == "C":
                        # Mix both L and R to center at reduced level
                        center_level = base_level_db + STEREO_TO_CENTER_OFFSET
                        if "L" in input_label_to_ch:
                            channel_map.append((input_label_to_ch["L"], out_ch, center_level))
                        if "R" in input_label_to_ch:
                            channel_map.append((input_label_to_ch["R"], out_ch, center_level))
                    elif out_label == "SL" and "L" in input_label_to_ch:
                        surround_level = base_level_db + STEREO_TO_SURROUND_OFFSET
                        channel_map.append((input_label_to_ch["L"], out_ch, surround_level))
                    elif out_label == "SR" and "R" in input_label_to_ch:
                        surround_level = base_level_db + STEREO_TO_SURROUND_OFFSET
                        channel_map.append((input_label_to_ch["R"], out_ch, surround_level))
                    elif out_label == "Sub":
                        # Skip sub - user handles via crossover on other outputs
                        logger.debug("Skipping Sub channel for stereo input (handled by crossover)")
                    else:
                        logger.debug(f"No stereo mapping for output label '{out_label}'")
                else:
                    logger.debug(f"No input channel for output label '{out_label}'")
        else:
            # No labels, map by position (1:1 or wrap if input has fewer channels)
            for i, out_ch in enumerate(output_audio.channels):
                in_idx = i % len(input_audio.channels)
                channel_map.append((input_audio.channels[in_idx], out_ch, base_level_db))
        
        return channel_map
    
    def set_zone_volume(
        self,
        input_audio: Optional[AudioMapping],
        output_audio: Optional[AudioMapping],
        level_db: float
    ) -> bool:
        """
        Set volume for all crosspoints in a zone.
        
        Maintains relative levels for stereo-to-surround downmix.
        Uses batch commands for faster updates.
        
        Args:
            input_audio: Current input audio mapping for the zone
            output_audio: Output audio mapping for the zone
            level_db: Base volume level in dB
            
        Returns:
            True if successful, False otherwise
        """
        if output_audio is None:
            return True
        
        if input_audio is None:
            logger.debug("No input routed to zone, skipping volume set")
            return True
        
        connection = self.get_connection(output_audio.tesira_unit)
        if not connection:
            return False
        
        unit = connection.unit
        # Build channel map with relative levels preserved
        channel_map = self._build_channel_map(input_audio, output_audio, level_db)
        
        # Build batch commands for all level changes
        commands: list[str] = []
        for in_ch, out_ch, ch_level in channel_map:
            ch_level = max(MIN_LEVEL_DB, min(MAX_LEVEL_DB, ch_level))
            commands.append(
                f"{unit.matrix_instance_tag} set crosspointLevel {in_ch} {out_ch} {ch_level:.1f}"
            )
        
        success = connection.send_commands_batch(commands)
        
        if success:
            logger.info(f"Set zone volume to base {level_db:.1f}dB for {len(channel_map)} crosspoints")
        
        return success


@contextmanager
def tesira_session(units: list[TesiraUnit]):
    """
    Context manager for Tesira controller sessions.
    
    Args:
        units: List of TesiraUnit configurations
        
    Yields:
        TesiraController instance
    """
    controller = TesiraController(units)
    try:
        controller.connect_all()
        yield controller
    finally:
        controller.disconnect_all()
