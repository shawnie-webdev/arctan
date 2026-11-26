import json

# 0-based bit widths: 'r' = 4 bits (0..15), 'n' = 8 bits (0..255)
datatype_len = {"r": 3, "n": 7}

with open("configs.json", "r", encoding="utf-8") as f:
    _cfg = json.load(f)
    inst_format = _cfg["inst_format"]
    instruction = {name: idx for idx, name in enumerate(inst_format)}


def assemble(asm_path: str, bin_path: str):
    out = bytearray()
    with open(asm_path, "r") as f:
        for line in f:
            line = line.split(";")[0].strip()  # Strip comments
            if not line:
                continue

            parts = line.replace(",", " ").split()
            mnemonic, operands = parts[0].upper(), parts[1:]

            word = (instruction[mnemonic] & 0xF) << 12
            cursor = 12
            for kind, raw_op in zip(inst_format[mnemonic], operands):
                bit_width = datatype_len[kind] + 1
                cursor -= bit_width
                val = int(raw_op[1:], 0) if kind == "r" else int(raw_op, 0)
                mask = (1 << bit_width) - 1
                word |= (val & mask) << cursor

            out.extend(word.to_bytes(2, byteorder="big"))

    with open(bin_path, "bw") as f:
        f.write(out)


assemble("nvfm_code.asm", "nvfm_code.bin")

with open("nvfm_code.bin", "br") as f:
    NVFM_PROGRAM_CODE = f.read()  # Ready for MemoryDevice(..., initial_data=NVFM_PROGRAM_CODE)