import os
import time
import random
import struct

class cpucode:
    @staticmethod
    def enc(a: str):
        return bytearray(a.encode())

    @staticmethod
    def dec(a: bytearray):
        return a.decode("utf-8")


def _extract_bits(val: int, start_bit: int, end_bit: int, max_bits: int) -> int:
    if not (0 <= start_bit <= end_bit < max_bits):
        raise ValueError(f"Bit range must satisfy 0 <= start_bit <= end_bit < {max_bits}")
    num_bits = end_bit - start_bit + 1
    mask = (1 << num_bits) - 1
    return (val >> start_bit) & mask


def _insert_bits(orig_val: int, start_bit: int, end_bit: int, new_bits: int, max_bits: int) -> int:
    if not (0 <= start_bit <= end_bit < max_bits):
        raise ValueError(f"Bit range must satisfy 0 <= start_bit <= end_bit < {max_bits}")
    num_bits = end_bit - start_bit + 1
    mask = (1 << num_bits) - 1
    cleared = orig_val & ~(mask << start_bit)
    return cleared | ((new_bits & mask) << start_bit)


class MemoryDevice:
    def __init__(self, size=1024, read_only=False, initial_data=None, endian: str = "big"):
        self.size = size
        self.read_only = read_only
        self.endian = endian
        self.f = 0  # From address (inclusive)
        self.t = 0  # To address (exclusive)

        if initial_data:
            if len(initial_data) > size:
                raise ValueError("Initial data exceeds device size")
            raw = bytearray(size)
            raw[: len(initial_data)] = initial_data
            self.mem = memoryview(bytes(raw) if read_only else raw)
        else:
            self.mem = memoryview(bytes(size) if read_only else bytearray(size))

    def get_bits(self, addr: int, start_bit: int, end_bit: int, num_bytes: int | None = None) -> int:
        """Extracts bits [start_bit..end_bit] starting at local device address `addr`."""
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if addr < 0 or addr + num_bytes > self.size:
            raise IndexError(f"Address range {hex(addr)}..{hex(addr + num_bytes)} out of device bounds")

        val = int.from_bytes(self.mem[addr : addr + num_bytes], byteorder=self.endian)
        return _extract_bits(val, start_bit, end_bit, num_bytes * 8)

    def set_bits(self, addr: int, start_bit: int, end_bit: int, value: int, num_bytes: int | None = None):
        """Updates bits [start_bit..end_bit] starting at local device address `addr`."""
        if self.read_only:
            raise PermissionError(f"Cannot write to read-only memory at {hex(addr)}")
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if addr < 0 or addr + num_bytes > self.size:
            raise IndexError(f"Address range {hex(addr)}..{hex(addr + num_bytes)} out of device bounds")

        orig_val = int.from_bytes(self.mem[addr : addr + num_bytes], byteorder=self.endian)
        updated_val = _insert_bits(orig_val, start_bit, end_bit, value, num_bytes * 8)
        self.mem[addr : addr + num_bytes] = updated_val.to_bytes(num_bytes, byteorder=self.endian)


class MemoryManager:
    def __init__(self, endian: str = "big"):
        self.devices = []
        self.max_addr = 0
        self.endian = endian

    def attach(self, device: MemoryDevice):
        device.f = self.max_addr
        device.t = self.max_addr + device.size
        device.endian = self.endian  # Keep device endianness synced with bus
        self.devices.append(device)
        self.max_addr = device.t

    def _find_device(self, addr: int) -> tuple[MemoryDevice, int]:
        for dev in self.devices:
            if dev.f <= addr < dev.t:
                return dev, addr - dev.f
        raise IndexError(f"Segmentation fault: unmapped address {hex(addr)}")

    def _write_single_byte(self, addr: int, byte_val: int):
        dev, offset = self._find_device(addr)
        if dev.read_only:
            raise PermissionError(f"Cannot write to read-only memory at {hex(addr)}")
        dev.mem[offset] = byte_val

    def __getitem__(self, addr: int | slice) -> int:
        if isinstance(addr, slice):
            # Normalize and bound the slice to this memory bus. This prevents
            # malformed/unbounded slices from iterating indefinitely.
            start, stop, step = addr.indices(self.max_addr)
            raw = bytearray()

            for address in range(start, stop, step):
                dev, offset = self._find_device(address)
                raw.append(dev.mem[offset])

            return int.from_bytes(raw, byteorder=self.endian)

        dev, offset = self._find_device(addr)
        return dev.mem[offset]

    def __setitem__(self, addr: int | slice, value: int):
        if isinstance(addr, slice):
            start = addr.start
            num_bytes = addr.stop - start
        else:
            start = addr
            num_bytes = max(1, (value.bit_length() + 7) // 8)

        raw_bytes = value.to_bytes(num_bytes, byteorder=self.endian)
        for i, byte_val in enumerate(raw_bytes):
            self._write_single_byte(start + i, byte_val)

    def get_bits(self, addr: int, start_bit: int, end_bit: int, num_bytes: int | None = None) -> int:
        """Extracts bits [start_bit..end_bit] starting at mapped bus address `addr`."""
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        val = self[addr : addr + num_bytes]
        return _extract_bits(val, start_bit, end_bit, num_bytes * 8)

    def set_bits(self, addr: int, start_bit: int, end_bit: int, value: int, num_bytes: int | None = None):
        """Updates bits [start_bit..end_bit] starting at mapped bus address `addr`."""
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        orig_val = self[addr : addr + num_bytes]
        updated_val = _insert_bits(orig_val, start_bit, end_bit, value, num_bytes * 8)
        self[addr : addr + num_bytes] = updated_val


class RegisterManager(MemoryManager):
    SPECIAL_REGS = {
        "RR": 13,   # Random register (fresh 64-bit value on read)
        "RTC": 14,  # Real-time clock (Unix timestamp / epoch ms)
        "CLK": 15,  # Clock / step counter
    }
    INDEX_TO_SPECIAL = {v: k for k, v in SPECIAL_REGS.items()}

    def __init__(self, endian: str = "big"):
        super().__init__(endian)
        self.clk_ticks = 0
        for _ in range(16):
            super().attach(MemoryDevice(size=8, endian=endian))

    def tick(self, steps: int = 1):
        """Advance the CLK register counter."""
        self.clk_ticks = (self.clk_ticks + steps) & 0xFFFFFFFFFFFFFFFF

    def _read_special_raw(self, name: str) -> int:
        if name == "RR":
            return random.getrandbits(64)
        if name == "RTC":
            return int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
        if name == "CLK":
            return self.clk_ticks & 0xFFFFFFFFFFFFFFFF
        raise KeyError(f"Unknown special register: {name}")

    def _find_device(self, addr: int) -> tuple[MemoryDevice, int]:
        # RegisterManager maps exactly one 8-byte device to each register.
        # Avoid scanning every device for every register access.
        if not 0 <= addr < 16 * 8:
            raise IndexError(f"Segmentation fault: unmapped register address {hex(addr)}")
        reg_idx, offset = divmod(addr, 8)
        return self.devices[reg_idx], offset

    def get_reg(self, reg_idx: int) -> int:
        if not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx} out of range (0-15)")

        dev = self.devices[reg_idx]
        return int.from_bytes(dev.mem[:8], byteorder=self.endian)

    def get_reg_as(self, reg_idx: int, kind: str = "n") -> float | int:
        """Read register `reg_idx` as:
        - 'f': float64 (IEEE 754 floating-point)
        - 's': signed 64-bit two's complement int
        - 'n': normal (unsigned) 64-bit int
        """
        raw = self.get_reg(reg_idx) & 0xFFFFFFFFFFFFFFFF
        fmt_prefix = ">" if self.endian == "big" else "<"

        match kind.lower():
            case "f":
                return struct.unpack(f"{fmt_prefix}d", struct.pack(f"{fmt_prefix}Q", raw))[0]
            case "s":
                return struct.unpack(f"{fmt_prefix}q", struct.pack(f"{fmt_prefix}Q", raw))[0]
            case "n":
                return raw
            case _:
                raise ValueError(f"Unknown format '{kind}': expected 'f', 's', or 'n'")

    def set_reg(self, reg_idx: int, value: int):
        if not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx} out of range (0-15)")
        if value < 0:
            value &= 0xFFFFFFFFFFFFFFFF
        dev = self.devices[reg_idx]
        dev.mem[:8] = (value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, byteorder=self.endian)

    def get_reg_bits(self, reg_idx: int, start_bit: int, end_bit: int) -> int:
        """Convenience wrapper to read bits [0..63] by register number (0-15)."""
        if not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx} out of range (0-15)")
        return super().get_bits(reg_idx * 8, start_bit, end_bit, num_bytes=8)

    def set_reg_bits(self, reg_idx: int, start_bit: int, end_bit: int, value: int):
        """Convenience wrapper to write bits [0..63] by register number (0-15)."""
        if not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx} out of range (0-15)")
        super().set_bits(reg_idx * 8, start_bit, end_bit, value, num_bytes=8)


class VideoMemory(MemoryDevice):
    def __init__(self, width: int = 128, height: int = 128):
        super().__init__(size=width * height, read_only=False)
        self.width = width
        self.height = height

    def to_rgb_bytes(self) -> bytes:
        """Converts 1-byte-per-pixel RGB332 data into a flat RGB24 byte buffer for fast rendering."""
        out = bytearray(self.size * 3)
        for i, byte in enumerate(self.mem):
            base = i * 3
            out[base] = ((byte >> 5) & 0b111) * 36      # R (0-252)
            out[base + 1] = ((byte >> 2) & 0b111) * 36  # G (0-252)
            out[base + 2] = (byte & 0b11) * 85          # B (0-255)
        return bytes(out)


""" CONFIGS (cfg) """

# 0-based: 'r' = 3 -> 4 bits (0..15), 'n' = 7 -> 8 bits (0..255)
datatype_len = {
    "r": 3,
    "n": 7,
}

import json

try:
    with open("configs.json", "r", encoding="utf-8") as f:
        _cfg = json.load(f)
        inst_format = _cfg["inst_format"]
        instruction = {name: idx for idx, name in enumerate(inst_format)}

except FileNotFoundError:
    raise FileNotFoundError("Config file not found")

OPCODE_TO_INSTR = {v: k for k, v in instruction.items()}

DEFAULT_ASM = """
SET R0 5
SET R1 10
ADD R0 R1 R2
PRINT R0
PRINT R1
PRINT R2
"""


def assemble_text(asm_text: str) -> bytes:
    out = bytearray()
    for line in asm_text.splitlines():
        code = line.split(";")[0].strip()
        if not code:
            continue
        parts = code.replace(",", " ").split()
        mnemonic, operands = parts[0].upper(), parts[1:]

        word = (instruction[mnemonic] & 0xF) << 12
        cursor = 12
        for kind, raw_op in zip(inst_format[mnemonic], operands):
            bit_len = datatype_len[kind] + 1
            cursor -= bit_len
            val = int(raw_op[1:]) if kind == "r" else int(raw_op)
            mask = (1 << bit_len) - 1
            word |= (val & mask) << cursor

        out.extend(word.to_bytes(2, byteorder="big"))
    return bytes(out)


""" Hardware Devices """


class Motherboard:
    def __init__(self, rom_bytes: bytes | bytearray, ram_size: int = 4096):
        self.rom_length = len(rom_bytes)
        self.nvfm = MemoryDevice(
            size=max(1024, self.rom_length),
            read_only=True,
            initial_data=rom_bytes,
            endian="big",
        )
        self.mmio = MemoryManager(endian="big")
        self.mmio.attach(MemoryDevice(size=ram_size, endian="big"))
        self.vram = VideoMemory(width=128, height=128)
        self.mmio.attach(self.vram)
        self.cpu = CPU(self)


class CPU:
    def __init__(self, motherboard: Motherboard):
        self.pc = 0
        self.is_reading_nvfm = True
        self.motherboard = motherboard
        self.rom_length = motherboard.rom_length
        self.register = RegisterManager(endian="big")
        self.eval_bit = False
        self.is_halted = False

    def _fetch_word(self) -> tuple[int, int]:
        addr = self.pc * 2
        if self.is_reading_nvfm:
            if addr < 0 or addr + 2 > self.rom_length:
                self.is_halted = True
                return 0, 16
            word = int.from_bytes(self.motherboard.nvfm.mem[addr : addr + 2], "big")
        else:
            word = self.motherboard.mmio[addr : addr + 2]
        return word, 16

    def step(self):
        word, width = self._fetch_word()
        opcode = _extract_bits(word, width - 4, width - 1, width)
        instr = OPCODE_TO_INSTR[opcode]

        cursor = width - 4
        args = []
        for kind in inst_format[instr]:
            bit_len = datatype_len[kind] + 1
            end_bit = cursor - 1
            start_bit = end_bit - bit_len + 1
            args.append(_extract_bits(word, start_bit, end_bit, width))
            cursor = start_bit

        match instr:
            case "SET":
                self.register.set_reg(args[0], args[1])
            case "MOV":
                self.register.set_reg(args[0], self.register.get_reg(args[1]))
            case "ADD":
                val = self.register.get_reg(args[0]) + self.register.get_reg(args[1])
                self.register.set_reg(args[2], val)
            case "SUB":
                val = self.register.get_reg(args[1]) - self.register.get_reg(args[2])
                self.register.set_reg(args[0], val)
            case "PRINT":
                print(self.register.get_reg(args[0]))
            case "EVAL_ET":
                self.eval_bit = self.register.get_reg(args[0]) == self.register.get_reg(args[1])
            case "EVAL_NET":
                self.eval_bit = self.register.get_reg(args[0]) != self.register.get_reg(args[1])
            case "EVAL_GT":
                self.eval_bit = self.register.get_reg(args[0]) < self.register.get_reg(args[1])
            case "EVAL_LT":
                self.eval_bit = self.register.get_reg(args[0]) > self.register.get_reg(args[1])
            case "EVAL_GTE":
                self.eval_bit = self.register.get_reg(args[0]) <= self.register.get_reg(args[1])
            case "EVAL_LTE":
                self.eval_bit = self.register.get_reg(args[0]) >= self.register.get_reg(args[1])
            case "JMP":
                self.pc = self.register.get_reg(args[0])
            case "JMPIF":
                if self.eval_bit == True:
                    self.pc = self.register.get_reg(args[0]) - 1
            case "HALT":
                self.is_halted = True

        self.pc += 1
        self.register.tick()


""" MAIN CODE """

if __name__ == "__main__":
    if os.path.exists("nvfm_code.asm"):
        with open("nvfm_code.asm", "r", encoding="utf-8") as f:
            program_bytes = assemble_text(f.read())
    else:
        program_bytes = assemble_text(DEFAULT_ASM)
        print("WARNING: NO NVFM BOOTLOADER CODE FOUND")
        print("WARNING: DEFAULTING TO DEFAULT_ASM")

    with open("nvfm_code.bin", "bw") as f:
        f.write(program_bytes)

    with open("nvfm_code.bin", "br") as f:
        NVFM_PROGRAM_CODE = f.read()

    board = Motherboard(NVFM_PROGRAM_CODE)
    num_instructions = len(NVFM_PROGRAM_CODE) // 2
    while board.cpu.is_halted == False:
        board.cpu.step()