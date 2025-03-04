namespayload = "..162E0580040000426564726F6F6D204170706C65205456...142E0580040100426C752052617920506C61796572...0D2E0580040200496E7075742033...0D2E0580040300496E7075742034...0D2E0580040400496E7075742035...0D2E0580040500496E7075742036...0D2E0580040600496E7075742037...0D2E0580040700496E7075742038...102E0580042000426564726F6F6D205456...112E0580042100426173656D656E74205456...0E2E05800422004F75747075742033...0E2E05800423004F75747075742034...142E05800424004F6666696365204D6F6E69746F72...0E2E05800425004F75747075742036...0D2E058004260053657276657273...0E2E05800427004F75747075742038...0E2E050004F5003030303030303030."
cardpayload = """   1: DMC-HD HDMI Input Card [v1.2911.00057, #0076C0FA] Stream:b0.0
   2: DMC-HD HDMI Input Card [v1.2911.00057, #0076C0F6] Stream:b1.0
   3: DMC-HD HDMI Input Card [v1.2911.00057, #0076C0F5] Stream:b2.0
   4: DMC-HD HDMI Input Card [v1.2911.00057, #00520C17] Stream:b3.0
   5: DMC-HD HDMI Input Card [v1.2911.00057, #0070993C] Stream:b4.0
   6: DMC-HD HDMI Input Card [v1.2911.00057, #00709C2F] Stream:b5.0
   7: DMC-C-DSP 8G STP DSP Input Card [v1.2911.00057, #007ABCBC] Stream:b6.0
  17: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #00764F25] Stream:c0.0
  18: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #00764F25] Stream:c0.1
  19: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #00764DD6] Stream:c2.0
  20: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #00764DD6] Stream:c2.1
  21: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #0073BBC7] Stream:c4.0
  22: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #0073BBC7] Stream:c4.1
  23: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #0073BBB4] Stream:c6.0
  24: DMC-CO-HD - 8G STP Output Card [v1.2625.00031, #0073BBB4] Stream:c6.1
  33: C2I-NVMEM
  35: C2I-ADGENET-1
  36: DMC-FPANEL Front Panel [v2.002.0026]"""

def parse_cards(cardPayload):
    cards = []
    for x in cardPayload.split("\n"):
        x = x.lstrip()
        x = x.split(":")
        if(x[1].__contains__("Input")):
            cards.append(["Input",x[0]])
        if(x[1].__contains__("Output")):
            cards.append(["Output",x[0]])
    return cards
def parseIO(cards,payload):
    inputs = []
    outputs = []
    for x in payload.split("..."):
        x = x.replace(".","")
        split = [x[i:i+2] for i in range(0, len(x), 2)]
        length = int(split[0],16)
        if len(split) != (length+1):
            break
        header = split[1:7]
        name = split[7:length+1]
        namestring = ""
        for x in name:
            namestring += x
        byte_array = bytes.fromhex(namestring)
        namestring = byte_array.decode('ascii')  # Or 'utf-8' if appropriate  
        pass
        for x in cards:
            if (int(header[4],16)+1 == int(x[1])) or (int(header[4],16)-15 == int(x[1])):
                if(x[0] == "Input"):
                    inputs.append([namestring,x[1]])
                if(x[0] == "Output"):
                    outputs.append([namestring,x[1],"No Input"])
    inputs.append(["No Input","0"])
    return inputs, outputs
cards = parse_cards(cardpayload)
inputs,outputs = parseIO(cards,namespayload)
