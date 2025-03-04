def parse_cards(cardPayload):
    cards = []
    cardPayload = cardPayload.decode().replace("cards -force\r\n\r\n","")
    for x in cardPayload.split("\r\n"):
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
    payload = payload.replace(b'\xfa',b'.')
    payload = payload.replace(b'\xfb',b'.')
    payload = payload.replace(b'\xd0',b'.')
    payload = payload.replace(b'\xc0',b'.')
    payload = payload.replace(b'DM-MD8x8>',b'')
    payload = payload.replace(b'\r\n',b'')
    payload = payload.decode()
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
