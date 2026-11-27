import json
import os
import random
import struct
import threading
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "configs.json"

try:
    with CONFIG_PATH.open("r", encoding="utf-8") as _config_file:
        ARCH_CONFIG = json.load(_config_file)
except FileNotFoundError as exc:
    raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}") from exc
except json.JSONDecodeError as exc:
    raise ValueError(f"Invalid JSON in {CONFIG_PATH}: {exc}") from exc

ARCHITECTURE = ARCH_CONFIG.get("architecture", {})
INSTRUCTION_BYTES = ARCHITECTURE.get("instruction_size_bytes", 3)
OPCODE_BITS = ARCHITECTURE.get("opcode_bits", 5)
ENDIANNESS = ARCHITECTURE.get("endianness", "big")
if not isinstance(INSTRUCTION_BYTES, int) or INSTRUCTION_BYTES < 1:
    raise ValueError("architecture.instruction_size_bytes must be positive")
if not isinstance(OPCODE_BITS, int) or OPCODE_BITS < 1:
    raise ValueError("architecture.opcode_bits must be positive")
if ENDIANNESS not in ("big", "little"):
    raise ValueError("architecture.endianness must be 'big' or 'little'")

INSTRUCTION_BITS = INSTRUCTION_BYTES * 8
OPERAND_BITS = INSTRUCTION_BITS - OPCODE_BITS
if OPERAND_BITS <= 0:
    raise ValueError("Instruction format leaves no bits for operands")
UINT64_MASK = (1 << 64) - 1


class cpucode:
    @staticmethod
    def enc(a: str):
        return bytearray(a.encode())

    @staticmethod
    def dec(a: bytearray):
        return a.decode("utf-8")


def _extract_bits(val: int, start_bit: int, end_bit: int, max_bits: int) -> int:
    if not (0 <= start_bit <= end_bit < max_bits):
        raise ValueError(
            f"Bit range must satisfy 0 <= start_bit <= end_bit < {max_bits}"
        )
    return (val >> start_bit) & ((1 << (end_bit - start_bit + 1)) - 1)


def _insert_bits(orig_val: int, start_bit: int, end_bit: int,
                 new_bits: int, max_bits: int) -> int:
    if not (0 <= start_bit <= end_bit < max_bits):
        raise ValueError(
            f"Bit range must satisfy 0 <= start_bit <= end_bit < {max_bits}"
        )
    width = end_bit - start_bit + 1
    if not isinstance(new_bits, int) or new_bits < 0 or new_bits >= (1 << width):
        raise ValueError(f"Value does not fit in {width} bits")
    mask = ((1 << width) - 1) << start_bit
    return (orig_val & ~mask) | (new_bits << start_bit)


class MemoryDevice:
    def __init__(self, size=1024, read_only=False, initial_data=None,
                 endian: str = "big"):
        if not isinstance(size, int) or size < 0:
            raise ValueError("Memory size must be a non-negative integer")
        if endian not in ("big", "little"):
            raise ValueError("Endian must be 'big' or 'little'")

        self.size = size
        self.read_only = read_only
        self.endian = endian
        self.f = 0
        self.t = 0

        if initial_data is not None:
            if len(initial_data) > size:
                raise ValueError("Initial data exceeds device size")
            raw = bytearray(size)
            raw[:len(initial_data)] = initial_data
        else:
            raw = bytearray(size)

        self.mem = memoryview(bytes(raw) if read_only else raw)

    def get_bits(self, addr: int, start_bit: int, end_bit: int,
                 num_bytes: int | None = None) -> int:
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if addr < 0 or num_bytes < 1 or addr + num_bytes > self.size:
            raise IndexError("Memory bit-read is outside the device")
        value = int.from_bytes(self.mem[addr:addr + num_bytes],
                               byteorder=self.endian)
        return _extract_bits(value, start_bit, end_bit, num_bytes * 8)

    def set_bits(self, addr: int, start_bit: int, end_bit: int, value: int,
                 num_bytes: int | None = None):
        if self.read_only:
            raise PermissionError("Cannot write to read-only memory")
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if addr < 0 or num_bytes < 1 or addr + num_bytes > self.size:
            raise IndexError("Memory bit-write is outside the device")
        original = int.from_bytes(self.mem[addr:addr + num_bytes],
                                  byteorder=self.endian)
        updated = _insert_bits(original, start_bit, end_bit, value,
                               num_bytes * 8)
        self.mem[addr:addr + num_bytes] = updated.to_bytes(
            num_bytes, byteorder=self.endian
        )


class MemoryManager:
    def __init__(self, endian: str = "big"):
        if endian not in ("big", "little"):
            raise ValueError("Endian must be 'big' or 'little'")
        self.devices = []
        self.max_addr = 0
        self.endian = endian

    def attach(self, device: MemoryDevice):
        if not isinstance(device, MemoryDevice):
            raise TypeError("Only MemoryDevice instances can be attached")
        device.f = self.max_addr
        device.t = self.max_addr + device.size
        device.endian = self.endian
        self.devices.append(device)
        self.max_addr = device.t

    def _find_device(self, addr: int) -> tuple[MemoryDevice, int]:
        if not isinstance(addr, int) or addr < 0 or addr >= self.max_addr:
            raise IndexError(f"Unmapped memory address: {addr!r}")
        for device in self.devices:
            if device.f <= addr < device.t:
                return device, addr - device.f
        raise IndexError(f"Unmapped memory address: {addr}")

    def __getitem__(self, addr: int | slice) -> int:
        if isinstance(addr, slice):
            if addr.step not in (None, 1):
                raise ValueError("Memory slices only support a step of 1")
            if addr.start is None or addr.stop is None:
                raise ValueError("Memory slices require explicit start and stop")
            start, stop = addr.start, addr.stop
            if not isinstance(start, int) or not isinstance(stop, int):
                raise TypeError("Memory slice bounds must be integers")
            if start < 0 or stop < start or stop > self.max_addr:
                raise IndexError("Memory slice is outside the address space")
            raw = bytearray()
            for address in range(start, stop):
                device, offset = self._find_device(address)
                raw.append(device.mem[offset])
            return int.from_bytes(raw, byteorder=self.endian)

        device, offset = self._find_device(addr)
        return device.mem[offset]

    def __setitem__(self, addr: int | slice, value: int):
        if not isinstance(value, int) or value < 0:
            raise ValueError("Memory values must be non-negative integers")

        if isinstance(addr, slice):
            if addr.step not in (None, 1):
                raise ValueError("Memory slices only support a step of 1")
            if addr.start is None or addr.stop is None:
                raise ValueError("Memory slices require explicit start and stop")
            start, stop = addr.start, addr.stop
            if not isinstance(start, int) or not isinstance(stop, int):
                raise TypeError("Memory slice bounds must be integers")
            if start < 0 or stop < start or stop > self.max_addr:
                raise IndexError("Memory slice is outside the address space")
            count = stop - start
            if count == 0:
                if value != 0:
                    raise ValueError("Cannot write a non-zero value to an empty slice")
                return
            if value.bit_length() > count * 8:
                raise OverflowError(f"Value does not fit in {count} bytes")
            raw = value.to_bytes(count, byteorder=self.endian)
            targets = [self._find_device(start + i) for i in range(count)]
            # Validate the entire write before changing any byte.
            for device, _ in targets:
                if device.read_only:
                    raise PermissionError("Cannot write to read-only memory")
            for (device, offset), byte_value in zip(targets, raw):
                device.mem[offset] = byte_value
            return

        if not isinstance(addr, int):
            raise TypeError("Memory address must be an integer or slice")
        if value > 0xFF:
            raise OverflowError("A single-byte memory write must be between 0 and 255")
        device, offset = self._find_device(addr)
        if device.read_only:
            raise PermissionError(f"Cannot write to read-only memory at {addr:#x}")
        device.mem[offset] = value

    def get_bits(self, addr: int, start_bit: int, end_bit: int,
                 num_bytes: int | None = None) -> int:
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if num_bytes < 1:
            raise ValueError("num_bytes must be positive")
        return _extract_bits(self[addr:addr + num_bytes], start_bit,
                             end_bit, num_bytes * 8)

    def set_bits(self, addr: int, start_bit: int, end_bit: int, value: int,
                 num_bytes: int | None = None):
        if num_bytes is None:
            num_bytes = (end_bit // 8) + 1
        if num_bytes < 1:
            raise ValueError("num_bytes must be positive")
        original = self[addr:addr + num_bytes]
        updated = _insert_bits(original, start_bit, end_bit, value,
                               num_bytes * 8)
        self[addr:addr + num_bytes] = updated


class RegisterManager(MemoryManager):
    SPECIAL_REGS = {"RR": 13, "RTC": 14, "CLK": 15}
    INDEX_TO_SPECIAL = {index: name for name, index in SPECIAL_REGS.items()}

    def __init__(self, endian: str = "big"):
        super().__init__(endian)
        self.clk_ticks = 0
        for _ in range(16):
            super().attach(MemoryDevice(size=8, endian=endian))

    def tick(self, steps: int = 1):
        self.clk_ticks = (self.clk_ticks + steps) & UINT64_MASK

    def _read_special_raw(self, name: str) -> int:
        if name == "RR":
            return random.getrandbits(64)
        if name == "RTC":
            return int(time.time() * 1000) & UINT64_MASK
        if name == "CLK":
            return self.clk_ticks
        raise KeyError(f"Unknown special register: {name}")

    def get_reg(self, reg_idx: int) -> int:
        if not isinstance(reg_idx, int) or not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx!r} out of range (0-15)")
        special = self.INDEX_TO_SPECIAL.get(reg_idx)
        if special is not None:
            return self._read_special_raw(special)
        return int.from_bytes(self.devices[reg_idx].mem[:8],
                              byteorder=self.endian)

    def get_reg_as(self, reg_idx: int, kind: str = "n") -> float | int:
        raw = self.get_reg(reg_idx) & UINT64_MASK
        prefix = ">" if self.endian == "big" else "<"
        kind = kind.lower()
        if kind == "n":
            return raw
        if kind == "s":
            return struct.unpack(f"{prefix}q", struct.pack(f"{prefix}Q", raw))[0]
        if kind == "f":
            return struct.unpack(f"{prefix}d", struct.pack(f"{prefix}Q", raw))[0]
        raise ValueError("Register kind must be 'n', 's', or 'f'")

    def set_reg(self, reg_idx: int, value: int):
        if not isinstance(reg_idx, int) or not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx!r} out of range (0-15)")
        if reg_idx in self.INDEX_TO_SPECIAL:
            raise PermissionError(f"R{reg_idx} is a read-only special register")
        if not isinstance(value, int):
            raise TypeError("Integer register writes require an integer")
        raw = (value & UINT64_MASK).to_bytes(8, byteorder=self.endian)
        self.devices[reg_idx].mem[:8] = raw

    def set_reg_as(self, reg_idx: int, value: float | int, kind: str = "n"):
        if not isinstance(reg_idx, int) or not 0 <= reg_idx < 16:
            raise IndexError(f"Register index {reg_idx!r} out of range (0-15)")
        if reg_idx in self.INDEX_TO_SPECIAL:
            raise PermissionError(f"R{reg_idx} is a read-only special register")
        prefix = ">" if self.endian == "big" else "<"
        kind = kind.lower()
        if kind == "f":
            raw = struct.pack(f"{prefix}d", float(value))
        elif kind == "s":
            raw = struct.pack(f"{prefix}Q", int(value) & UINT64_MASK)
        elif kind == "n":
            raw = (int(value) & UINT64_MASK).to_bytes(8, byteorder=self.endian)
        else:
            raise ValueError("Register kind must be 'n', 's', or 'f'")
        self.devices[reg_idx].mem[:8] = raw

    def get_reg_bits(self, reg_idx: int, start_bit: int, end_bit: int) -> int:
        if not 0 <= reg_idx < 16:
            raise IndexError("Register index out of range (0-15)")
        return _extract_bits(self.get_reg(reg_idx), start_bit, end_bit, 64)

    def set_reg_bits(self, reg_idx: int, start_bit: int, end_bit: int,
                     value: int):
        if not 0 <= reg_idx < 16:
            raise IndexError("Register index out of range (0-15)")
        if reg_idx in self.INDEX_TO_SPECIAL:
            raise PermissionError(f"R{reg_idx} is a read-only special register")
        original = self.get_reg(reg_idx)
        updated = _insert_bits(original, start_bit, end_bit, value, 64)
        self.set_reg(reg_idx, updated)


class VideoMemory(MemoryDevice):
    def __init__(self, width: int = 128, height: int = 128):
        super().__init__(size=width * height, read_only=False)
        self.width = width
        self.height = height

    def to_rgb_bytes(self) -> bytes:
        out = bytearray(self.size * 3)
        for index, pixel in enumerate(self.mem):
            base = index * 3
            red = (pixel >> 5) & 0b111
            green = (pixel >> 2) & 0b111
            blue = pixel & 0b11
            out[base] = (red * 255) // 7
            out[base + 1] = (green * 255) // 7
            out[base + 2] = blue * 85
        return bytes(out)

    def start_screen(self, scale: int = 4, fps: int = 60,
                     stop_event: threading.Event | None = None):
        # Pygame's display/event loop stays on the main thread.
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError(
                "Pygame is required for the Arctan64 display. "
                "Install it with: python3 -m pip install pygame"
            ) from exc

        if scale < 1 or fps < 1:
            raise ValueError("scale and fps must be positive")

        pygame.init()
        screen = pygame.display.set_mode(
            (self.width * scale, self.height * scale)
        )
        pygame.display.set_caption("Arctan64 Display")
        clock = pygame.time.Clock()
        running = True

        try:
            while running and (stop_event is None or not stop_event.is_set()):
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False

                rgb = self.to_rgb_bytes()
                surface = pygame.image.frombuffer(
                    rgb, (self.width, self.height), "RGB"
                )
                scaled = pygame.transform.scale(
                    surface, (self.width * scale, self.height * scale)
                )
                screen.blit(scaled, (0, 0))
                pygame.display.flip()
                clock.tick(fps)
        finally:
            pygame.quit()


# Load and validate the instruction table from the shared architecture config.
inst_format = ARCH_CONFIG.get("inst_format")
if not isinstance(inst_format, dict) or not inst_format:
    raise ValueError("configs.json must contain a non-empty 'inst_format' object")

_operand_types = ARCH_CONFIG.get("operand_types", {
    "r": {"bits": 4},
    "n": {"bits": 8},
})
datatype_len = {
    kind: definition.get("bits") if isinstance(definition, dict) else definition
    for kind, definition in _operand_types.items()
}
if any(not isinstance(bits, int) or bits < 1 for bits in datatype_len.values()):
    raise ValueError("Every operand type must define a positive bit width")

instruction = {}
for name, fmt in inst_format.items():
    mnemonic = name.upper()
    if mnemonic in instruction:
        raise ValueError(f"Duplicate instruction mnemonic: {mnemonic}")
    if not isinstance(fmt, str) or any(kind not in datatype_len for kind in fmt):
        raise ValueError(f"Invalid operand format for {mnemonic}: {fmt!r}")
    if sum(datatype_len[kind] for kind in fmt) > OPERAND_BITS:
        raise ValueError(
            f"{mnemonic} needs more than {OPERAND_BITS} operand bits"
        )
    instruction[mnemonic] = len(instruction)

if len(instruction) > (1 << OPCODE_BITS):
    raise ValueError(
        f"Too many instructions ({len(instruction)}); "
        f"{OPCODE_BITS}-bit opcodes support at most {1 << OPCODE_BITS}"
    )

OPCODE_TO_INSTR = {opcode: mnemonic for mnemonic, opcode in instruction.items()}


DEFAULT_ASM = """
SET R0 5
SET R1 10
ADD R0 R1 R2
PRINT R0
PRINT R1
PRINT R2
HALT
"""


def _parse_register(token: str, line_number: int) -> int:
    token = token.upper()
    if not token.startswith("R") or not token[1:].isdigit():
        raise ValueError(f"Line {line_number}: expected a register like R0, got {token!r}")
    value = int(token[1:])
    if not 0 <= value <= 15:
        raise ValueError(f"Line {line_number}: register index must be 0..15")
    return value


def assemble_text(asm_text: str) -> bytes:
    if not isinstance(asm_text, str):
        raise TypeError("Assembly source must be text")

    output = bytearray()
    for line_number, line in enumerate(asm_text.splitlines(), start=1):
        code = line.split(";", 1)[0].strip()
        if not code:
            continue

        parts = code.replace(",", " ").split()
        mnemonic = parts[0].upper()
        operands = parts[1:]

        if mnemonic not in instruction:
            raise ValueError(f"Line {line_number}: unknown instruction {mnemonic!r}")

        fmt = inst_format[mnemonic]
        if len(operands) != len(fmt):
            raise ValueError(
                f"Line {line_number}: {mnemonic} expects {len(fmt)} operand(s), "
                f"got {len(operands)}"
            )

        word = instruction[mnemonic] << OPERAND_BITS
        cursor = OPERAND_BITS

        for kind, raw_operand in zip(fmt, operands):
            width = datatype_len[kind]
            cursor -= width

            if kind == "r":
                value = _parse_register(raw_operand, line_number)
            else:
                try:
                    value = int(raw_operand, 0)
                except ValueError as exc:
                    raise ValueError(
                        f"Line {line_number}: invalid immediate {raw_operand!r}"
                    ) from exc

            if not 0 <= value < (1 << width):
                raise ValueError(
                    f"Line {line_number}: operand {raw_operand!r} "
                    f"does not fit in {width} bits"
                )
            word |= value << cursor

        output.extend(word.to_bytes(INSTRUCTION_BYTES, byteorder=ENDIANNESS))

    return bytes(output)


class Motherboard:
    def __init__(self, rom_bytes: bytes | bytearray, ram_size: int = 4096):
        self.rom_length = len(rom_bytes)
        if self.rom_length % INSTRUCTION_BYTES:
            raise ValueError(
                f"ROM length must be a multiple of {INSTRUCTION_BYTES} bytes"
            )

        self.nvfm = MemoryDevice(
            size=max(1024, self.rom_length),
            read_only=True,
            initial_data=rom_bytes,
            endian=ENDIANNESS,
        )
        self.mmio = MemoryManager(endian=ENDIANNESS)
        self.mmio.attach(MemoryDevice(size=ram_size, endian=ENDIANNESS))
        self.fbuffer = VideoMemory(width=128, height=128)
        self.mmio.attach(self.fbuffer)
        self.cpu = CPU(self)

    def poweron(self, show_screen: bool = True, scale: int = 4, fps: int = 60):
        self.cpu.is_halted = False

        if not show_screen:
            self.cpu.run()
            return

        errors = []

        def cpu_worker():
            try:
                self.cpu.run()
            except BaseException as exc:
                errors.append(exc)
                self.cpu.is_halted = True
            finally:
                # Stop the display loop when the CPU halts, reaches ROM end,
                # or raises an execution error.
                self.cpu.stop_event.set()

        worker = threading.Thread(target=cpu_worker, name="Arctan64-CPU")
        worker.start()

        try:
            self.fbuffer.start_screen(
                scale=scale, fps=fps, stop_event=self.cpu.stop_event
            )
        except RuntimeError as exc:
            # A missing Pygame installation should not prevent headless execution.
            print(f"Display unavailable: {exc}")
            worker.join()
        else:
            if not self.cpu.is_halted:
                self.cpu.stop_event.set()
                self.cpu.is_halted = True
            worker.join()

        if errors:
            raise errors[0]


class CPU:
    def __init__(self, motherboard: Motherboard):
        self.pc = 0
        self.motherboard = motherboard
        self.rom_length = motherboard.rom_length
        self.register = RegisterManager(endian=ENDIANNESS)
        self.eval_bit = False
        self.is_halted = False
        self.stop_event = threading.Event()

    def request_stop(self):
        self.is_halted = True
        self.stop_event.set()

    def _fetch_word(self) -> int | None:
        address = self.pc * INSTRUCTION_BYTES

        if self.pc < 0:
            raise IndexError(f"Program counter cannot be negative: {self.pc}")

        if address + INSTRUCTION_BYTES > self.rom_length:
            self.is_halted = True
            return None

        raw = self.motherboard.nvfm.mem[
            address:address + INSTRUCTION_BYTES
        ]
        return int.from_bytes(raw, byteorder=ENDIANNESS)

    @staticmethod
    def _signed_divide(left: int, right: int) -> int:
        if right == 0:
            raise ZeroDivisionError("Arctan64 integer division by zero")
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient

    def step(self):
        if self.is_halted:
            return

        word = self._fetch_word()
        if word is None:
            return

        opcode = _extract_bits(
            word, OPERAND_BITS, INSTRUCTION_BITS - 1, INSTRUCTION_BITS
        )
        mnemonic = OPCODE_TO_INSTR.get(opcode)
        if mnemonic is None:
            raise ValueError(
                f"Invalid opcode {opcode} at instruction address {self.pc}"
            )

        fmt = inst_format[mnemonic]
        cursor = OPERAND_BITS
        args = []

        for kind in fmt:
            width = datatype_len[kind]
            cursor -= width
            args.append(_extract_bits(word, cursor, cursor + width - 1,
                                      INSTRUCTION_BITS))

        next_pc = self.pc + 1

        match mnemonic:
            case "SET":
                self.register.set_reg(args[0], args[1])

            case "MOV":
                self.register.set_reg(args[0], self.register.get_reg(args[1]))

            case "ADDI":
                self.register.set_reg(
                    args[2], self.register.get_reg(args[0]) + args[1]
                )

            case "ADD":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg(args[0]) + self.register.get_reg(args[1])
                )
            case "SUB":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg(args[0]) - self.register.get_reg(args[1])
                )
            case "MUL":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg(args[0]) * self.register.get_reg(args[1])
                )
            case "DIV":
                divisor = self.register.get_reg(args[1])
                if divisor == 0:
                    raise ZeroDivisionError("DIV by zero")
                self.register.set_reg(
                    args[2], self.register.get_reg(args[0]) // divisor
                )

            case "SADD":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg_as(args[0], "s")
                    + self.register.get_reg_as(args[1], "s")
                )
            case "SSUB":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg_as(args[0], "s")
                    - self.register.get_reg_as(args[1], "s")
                )
            case "SMUL":
                self.register.set_reg(
                    args[2],
                    self.register.get_reg_as(args[0], "s")
                    * self.register.get_reg_as(args[1], "s")
                )
            case "SDIV":
                left = self.register.get_reg_as(args[0], "s")
                right = self.register.get_reg_as(args[1], "s")
                self.register.set_reg(args[2], self._signed_divide(left, right))

            case "PRINT":
                print(self.register.get_reg(args[0]))

            case "EVAL_ET":
                self.eval_bit = (
                    self.register.get_reg(args[0]) == self.register.get_reg(args[1])
                )
            case "EVAL_NET":
                self.eval_bit = (
                    self.register.get_reg(args[0]) != self.register.get_reg(args[1])
                )
            case "EVAL_GT":
                self.eval_bit = (
                    self.register.get_reg(args[0]) > self.register.get_reg(args[1])
                )
            case "EVAL_LT":
                self.eval_bit = (
                    self.register.get_reg(args[0]) < self.register.get_reg(args[1])
                )
            case "EVAL_GTE":
                self.eval_bit = (
                    self.register.get_reg(args[0]) >= self.register.get_reg(args[1])
                )
            case "EVAL_LTE":
                self.eval_bit = (
                    self.register.get_reg(args[0]) <= self.register.get_reg(args[1])
                )

            case "JMP":
                next_pc = self.register.get_reg(args[0])
            case "JMPIF":
                if self.eval_bit:
                    next_pc = self.register.get_reg(args[0])

            case "PUSHFLAG":
                # Store the current condition flag (0 or 1) in the given register.
                self.register.set_reg(args[0], int(self.eval_bit))

            case "HALT":
                self.is_halted = True

            case _:
                raise NotImplementedError(
                    f"Instruction {mnemonic} has no CPU implementation"
                )

        self.pc = next_pc
        self.register.tick()

    def run(self):
        while not self.is_halted and not self.stop_event.is_set():
            self.step()


def main():
    asm_path = BASE_DIR / "nvfm_code.asm"
    bin_path = BASE_DIR / "nvfm_code.bin"

    if asm_path.exists():
        assembly = asm_path.read_text(encoding="utf-8")
    else:
        assembly = DEFAULT_ASM
        print("WARNING: NO NVFM BOOTLOADER CODE FOUND")
        print("WARNING: DEFAULTING TO DEFAULT_ASM")

    program_bytes = assemble_text(assembly)
    bin_path.write_bytes(program_bytes)

    # Re-read the binary just as the original emulator did, so this file is
    # always the exact byte sequence supplied to the motherboard.
    program_bytes = bin_path.read_bytes()
    board = Motherboard(program_bytes)
    board.poweron(show_screen=True)


if __name__ == "__main__":
    main()
