"""Arctan64 assembler.

The assembler reads the instruction layout from configs.json.
Run:
    python3 compile.py [source.asm] [output.bin]
Defaults to nvfm_code.asm and nvfm_code.bin in this directory.
"""

import argparse
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "configs.json"


def load_config(config_path=DEFAULT_CONFIG):
    config_path = Path(config_path)
    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc

    architecture = config.get("architecture", {})
    instruction_bytes = architecture.get("instruction_size_bytes", 3)
    opcode_bits = architecture.get("opcode_bits", 5)
    endian = architecture.get("endianness", "big")
    operand_types = config.get("operand_types", {
        "r": {"bits": 4},
        "n": {"bits": 8},
    })
    inst_format = config.get("inst_format")

    if not isinstance(instruction_bytes, int) or instruction_bytes < 1:
        raise ValueError("architecture.instruction_size_bytes must be a positive integer")
    if not isinstance(opcode_bits, int) or opcode_bits < 1:
        raise ValueError("architecture.opcode_bits must be a positive integer")
    if endian not in ("big", "little"):
        raise ValueError("architecture.endianness must be 'big' or 'little'")
    if not isinstance(inst_format, dict) or not inst_format:
        raise ValueError("configs.json must contain a non-empty inst_format object")

    instruction_bits = instruction_bytes * 8
    operand_bits = instruction_bits - opcode_bits
    if operand_bits <= 0:
        raise ValueError("Instruction must have at least one operand bit")

    datatype_len = {}
    for kind, definition in operand_types.items():
        bits = definition.get("bits") if isinstance(definition, dict) else definition
        if not isinstance(bits, int) or bits < 1:
            raise ValueError(f"Operand type {kind!r} must define a positive bit width")
        datatype_len[kind] = bits

    instructions = {}
    for name, fmt in inst_format.items():
        mnemonic = name.upper()
        if mnemonic in instructions:
            raise ValueError(f"Duplicate instruction mnemonic: {mnemonic}")
        if not isinstance(fmt, str):
            raise ValueError(f"Format for {mnemonic} must be a string")
        if any(kind not in datatype_len for kind in fmt):
            raise ValueError(f"Unknown operand type in format for {mnemonic}: {fmt!r}")
        if sum(datatype_len[kind] for kind in fmt) > operand_bits:
            raise ValueError(
                f"{mnemonic} needs {sum(datatype_len[kind] for kind in fmt)} "
                f"operand bits, but only {operand_bits} are available"
            )
        instructions[mnemonic] = len(instructions)

    if len(instructions) > (1 << opcode_bits):
        raise ValueError(
            f"{len(instructions)} instructions exceed the {opcode_bits}-bit opcode capacity"
        )

    return {
        "instruction_bytes": instruction_bytes,
        "instruction_bits": instruction_bits,
        "opcode_bits": opcode_bits,
        "operand_bits": operand_bits,
        "endianness": endian,
        "datatype_len": datatype_len,
        "inst_format": {name.upper(): fmt for name, fmt in inst_format.items()},
        "instruction": instructions,
    }


def parse_register(token, line_number):
    token = token.upper()
    if not token.startswith("R") or not token[1:].isdigit():
        raise ValueError(
            f"Line {line_number}: expected register syntax R0..R15, got {token!r}"
        )
    value = int(token[1:])
    if not 0 <= value <= 15:
        raise ValueError(f"Line {line_number}: register index must be between 0 and 15")
    return value


def assemble_text(asm_text, config=None):
    if config is None:
        config = load_config()

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

        if mnemonic not in config["instruction"]:
            raise ValueError(f"Line {line_number}: unknown instruction {mnemonic!r}")

        fmt = config["inst_format"][mnemonic]
        if len(operands) != len(fmt):
            raise ValueError(
                f"Line {line_number}: {mnemonic} expects {len(fmt)} operand(s), "
                f"got {len(operands)}"
            )

        word = config["instruction"][mnemonic] << config["operand_bits"]
        cursor = config["operand_bits"]

        for kind, raw_operand in zip(fmt, operands):
            width = config["datatype_len"][kind]
            cursor -= width

            if kind == "r":
                value = parse_register(raw_operand, line_number)
            else:
                try:
                    value = int(raw_operand, 0)
                except ValueError as exc:
                    raise ValueError(
                        f"Line {line_number}: invalid immediate {raw_operand!r}"
                    ) from exc

            if not 0 <= value < (1 << width):
                raise ValueError(
                    f"Line {line_number}: operand {raw_operand!r} does not fit "
                    f"in {width} bits"
                )
            word |= value << cursor

        output.extend(
            word.to_bytes(
                config["instruction_bytes"],
                byteorder=config["endianness"],
            )
        )

    return bytes(output)


def assemble(asm_path, bin_path, config_path=DEFAULT_CONFIG):
    config = load_config(config_path)
    asm_path = Path(asm_path)
    bin_path = Path(bin_path)

    try:
        source = asm_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Assembly source not found: {asm_path}") from exc

    binary = assemble_text(source, config)
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_bytes(binary)
    return binary


def main():
    parser = argparse.ArgumentParser(description="Assemble Arctan64 assembly into binary")
    parser.add_argument("source", nargs="?", default=BASE_DIR / "nvfm_code.asm")
    parser.add_argument("output", nargs="?", default=BASE_DIR / "nvfm_code.bin")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()

    binary = assemble(args.source, args.output, args.config)
    config = load_config(args.config)
    print(
        f"Assembled {len(binary) // config['instruction_bytes']} instruction(s) "
        f"({len(binary)} bytes) -> {args.output}"
    )


if __name__ == "__main__":
    main()
