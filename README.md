# Arctan64

Arctan64 is a small 64-bit CPU emulator with a configurable instruction set.

## Requirements

- Python 3.10 or newer
- Pygame for the graphical framebuffer window

Install Pygame:

```bash
python3 -m pip install pygame
```

## Run

Assemble the default program:

```bash
python3 compile.py
```

This reads `nvfm_code.asm` and writes `nvfm_code.bin`.

You can also specify paths:

```bash
python3 compile.py program.asm program.bin
```

Run the emulator:

```bash
python3 main.py
```

The emulator assembles `nvfm_code.asm` itself when it starts. If that file does not exist, it runs the built-in demonstration program.

## Instruction encoding

The encoding is defined in `configs.json`, which is shared by the assembler and emulator.

The current architecture uses:

| Property | Value |
|---|---:|
| Instruction size | 3 bytes (24 bits) |
| Opcode | 5 bits |
| Operand area | 19 bits |
| Registers | 16 |
| Register width | 64 bits |
| Byte order | Big-endian |

The 5-bit opcode supports up to 32 distinct instructions. Each configured instruction is assigned an opcode based on its order in `inst_format`. Keep the instruction order unchanged if you need existing binary files to retain their opcode assignments.

Operand types:

| Type | Width | Meaning |
|---|---:|---|
| `r` | 4 bits | Register index, R0-R15 |
| `n` | 8 bits | Unsigned immediate, 0-255 |

Operand formats are written in order. For example, `rrr` means three register operands and `rn` means a register followed by an immediate.

## Configured instructions

| Instruction | Format | Purpose |
|---|---|---|
| `MOV` | `rr` | Copy one register into another |
| `SET` | `rn` | Set a register to an immediate |
| `ADD`, `SUB`, `MUL`, `DIV` | `rrr` | Unsigned arithmetic |
| `SADD`, `SSUB`, `SMUL`, `SDIV` | `rrr` | Signed arithmetic |
| `PRINT` | `r` | Print a register value |
| `EVAL_ET`, `EVAL_NET` | `rr` | Equality / inequality comparison |
| `EVAL_LT`, `EVAL_GT`, `EVAL_LTE`, `EVAL_GTE` | `rr` | Relational comparison |
| `JMP` | `r` | Jump to the address in a register |
| `JMPIF` | `r` | Jump if the evaluation flag is true |
| `PUSHFLAG` | `r` | Copy the evaluation flag (0 or 1) to a register |
| `HALT` | empty | Stop the CPU |

## Registers

- R0-R12 are general-purpose registers.
- R13 (RR) returns a new random 64-bit value when read.
- R14 (RTC) returns the current Unix timestamp in milliseconds.
- R15 (CLK) returns the CPU instruction tick count.

The special registers are read-only.

## Assembly example

```asm
SET R0 5
SET R1 10
ADD R0 R1 R2
EVAL_GT R1 R0
PUSHFLAG R3
PRINT R2
PRINT R3
HALT
```

Immediate values currently support decimal and Python-style prefixed integer notation, such as `0x10`. Register operands must use the `R0` through `R15` form. Comments begin with a semicolon.

## Binary compatibility

The current instruction format is 24-bit. Binaries assembled for the earlier 16-bit format are not compatible. Reassemble source files with the current `compile.py` or let `main.py` rebuild the binary from `nvfm_code.asm`.
