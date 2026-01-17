from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

def parse_cards(payload: bytes) -> List[Tuple[str, str]]:
    """
    Parse card information from Crestron switcher response.
    
    Args:
        payload: Raw bytes from switcher 'cards' command
        
    Returns:
        List of tuples: [("Input"|"Output", card_id), ...]
    """
    try:
        cards: List[Tuple[str, str]] = []
        payload_str = payload.decode('utf-8', errors='replace')
        payload_str = payload_str.replace("cards\r\n\r\n", "")
        
        for line in payload_str.split("\r\n"):
            line = line.lstrip()
            if not line:
                continue
                
            parts = line.split(":")
            if len(parts) > 1:
                card_id = parts[0].strip()
                card_info = parts[1]
                
                if "Input" in card_info:
                    cards.append(("Input", card_id))
                if "Output" in card_info:
                    cards.append(("Output", card_id))
                    
        return cards
    except Exception as e:
        logger.error(f"Error parsing cards: {e}")
        return []

def parseIO(cards: List[Tuple[str, str]], payload: bytes, cmd: bytes) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
    """
    Parse input/output names from Crestron switcher response.
    
    Args:
        cards: List of card tuples from parse_cards
        payload: Raw bytes from switcher response
        cmd: Command that was sent (to remove from response)
        
    Returns:
        Tuple of (inputs, outputs) where:
        - inputs: List of (name, card_id) tuples
        - outputs: List of (name, card_id, current_input) tuples
    """
    inputs: List[Tuple[str, str]] = []
    outputs: List[Tuple[str, str, str]] = []
    
    try:
        # Clean up payload
        payload_str = payload.decode('utf-8', errors='replace')
        payload_str = payload_str.replace('\xfa', '.')
        payload_str = payload_str.replace('\xfb', '.')
        payload_str = payload_str.replace('\xd0', '.')
        payload_str = payload_str.replace('\xc0', '.')
        payload_str = payload_str.replace('DM-MD8x8>', '')
        payload_str = payload_str.replace('\r\n', '')
        
        cmd_str = cmd.decode('utf-8', errors='replace').replace('\r\n', '')
        payload_str = payload_str.replace(cmd_str + "..", '')
        
        # Parse each entry
        for entry in payload_str.split("..."):
            if not entry:
                continue
                
            entry_clean = entry.replace(".", "")
            if not entry_clean:
                continue
            
            # Split into hex pairs
            hex_pairs = [entry_clean[i:i+2] for i in range(0, len(entry_clean), 2)]
            if len(hex_pairs) < 7:
                continue
            
            try:
                length = int(hex_pairs[0], 16)
                if len(hex_pairs) != (length + 1):
                    break
                    
                header = hex_pairs[1:7]
                name_hex = hex_pairs[7:length+1]
                
                # Convert hex to string
                hex_string = "".join(name_hex)
                try:
                    byte_array = bytes.fromhex(hex_string)
                    name = byte_array.decode('ascii')
                except (ValueError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to decode name from hex {hex_string}: {e}")
                    continue
                
                # Match to card
                try:
                    header_card_id = int(header[4], 16)
                    for card_type, card_id in cards:
                        card_id_int = int(card_id)
                        if (header_card_id + 1 == card_id_int) or (header_card_id - 15 == card_id_int):
                            if card_type == "Input":
                                inputs.append((name, card_id))
                            elif card_type == "Output":
                                outputs.append((name, card_id, "0"))  # Default to no input
                            break
                except (ValueError, IndexError) as e:
                    logger.warning(f"Error matching card: {e}")
                    continue
                    
            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing entry: {e}")
                continue
        
        # Add default "No Input" option
        inputs.append(("No Input", "0"))
        
        return inputs, outputs
        
    except Exception as e:
        logger.error(f"Error parsing I/O: {e}")
        return [], []

def createMonitors(outputs: List[Tuple[str, str, str]], tn) -> None:
    """
    Create monitors for outputs (currently unused).
    
    Args:
        outputs: List of output tuples
        tn: Telnet connection object
    """
    for output in outputs:
        cmd = f"JOINGETOUTSERIAL {output[1]} {output[1]}\r\n"
        try:
            tn.write(cmd.encode())
            tn.read_until(b"DM-MD8x8>", timeout=2)
        except Exception as e:
            logger.error(f"Error creating monitor for {output[0]}: {e}")

def parse_monitor(payload: bytes) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse monitor response to extract output and input IDs.
    
    Args:
        payload: Raw bytes from monitor response
        
    Returns:
        Tuple of (output_id, input_id) or (None, None) if not found
    """
    try:
        payload_str = payload.replace(b'\xfa', b'.')
        payload_str = payload_str.replace(b'\xfb', b'.')
        payload_str = payload_str.replace(b'\xd0', b'.')
        payload_str = payload_str.replace(b'\xc0', b'.')
        payload_str = payload_str.replace(b'DM-MD8x8>', b'')
        payload_str = payload_str.replace(b'\r\n', b'')
        payload_str = payload_str.decode('utf-8', errors='replace')
        
        for entry in payload_str.split("..."):
            if not entry:
                continue
                
            entry_clean = entry.replace(".", "")
            if not entry_clean:
                continue
            
            hex_pairs = [entry_clean[i:i+2] for i in range(0, len(entry_clean), 2)]
            if len(hex_pairs) < 4:
                continue
            
            try:
                length = int(hex_pairs[0], 16)
                if len(hex_pairs) != (length + 1):
                    continue
                
                # Check for monitor message pattern
                if (len(hex_pairs) > length - 2 and
                    hex_pairs[1] == "2E" and 
                    hex_pairs[2] == "03" and 
                    hex_pairs[3] == "00" and 
                    hex_pairs[length - 3] == "27" and 
                    hex_pairs[length - 2] == "02"):
                    output_id = hex_pairs[4]
                    input_id = hex_pairs[length - 1]
                    return output_id, input_id
            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing monitor entry: {e}")
                continue
        
        return None, None
        
    except Exception as e:
        logger.error(f"Error parsing monitor: {e}")
        return None, None