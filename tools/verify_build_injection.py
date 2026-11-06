import struct, zlib, marshal

exe = r"C:\Users\Lenovo\Desktop\code\ORBYTE\release\ORBYTE-1.0.0.exe"
data = open(exe, "rb").read()
i = data.find(b"PYZ\x00")
print("PYZ at", i)
toc_off = struct.unpack("!i", data[i + 8:i + 12])[0]
toc = marshal.loads(data[i + toc_off:])
if isinstance(toc, dict):
    toc = [(k, v) for k, v in toc.items()]
for name, (typ, pos, length) in toc:
    if name in ("orbyte._build_info", "orbyte.__init__"):
        obj = marshal.loads(zlib.decompress(data[i + pos:i + pos + length]))
        print("=====", name)
        if name == "orbyte._build_info":
            code = obj if hasattr(obj, "co_consts") else obj[0]
            print("constants:", [c for c in code.co_consts if isinstance(c, str)])
        else:
            code = obj if hasattr(obj, "co_consts") else obj[0]
            print("constants:", [c for c in code.co_consts if isinstance(c, str)])
