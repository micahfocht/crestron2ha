def parse_cards(payload):
    try:
        cards = []
        payload = payload.decode().replace("cards\r\n\r\n","")
        for x in payload.split("\r\n"):
            x = x.lstrip()
            x = x.split(":")
            if len(x) > 1:
                if(x[1].__contains__("Input")):
                    cards.append(["Input",x[0]])
                if(x[1].__contains__("Output")):
                    cards.append(["Output",x[0]])
        return cards
    except Exception as e:
        print(f"Error parsing cards: {e}")
        return []

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
                    outputs.append([namestring,x[1],0])
    inputs.append(["No Input","0"])
    return inputs, outputs

def createMonitors(outputs,tn):
    for x in outputs:
        cmd = "JOINGETOUTSErial " + x[1] + " " + x[1] + "\r\n"
        tn.write(cmd.encode())
        tn.read_until(b"DM-MD8x8>",timeout=2)

def parse_monitor(payload):
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
        if(split[1] == "2E" and split[2] == "03" and split[3] =="00" and split[length -3] == "27" and split[length -2] == "02"):
            output = split[4]
            input = split[length -1]
            pass
    return output,input

#print(parse_monitor(b"0C2E030001A402360027020100"))
#print(parse_monitor(b"..082E0100012602EE02...082E030001DC013600...092E080001F100030000...092E0800015501030000...082E0100012682EE82...0C2E030001A402360027020100."))
#print(parse_monitor(b"..062E0100022602...082E03000227020000."))