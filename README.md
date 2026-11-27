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

The encoding is defined in `configs.json`, shared by the assembler and emulator.

| Property | Value |
|---|---:|
| Instruction size | 3 bytes (24 bits) |
| Opcode | 6 bits |
| Operand area | 18 bits |
| Registers | 16 |
| Register width | 64 bits |
| Byte order | Big-endian |

The 6-bit opcode supports 64 distinct instructions. Opcodes are assigned by the order of entries in `inst_format`. New instructions are appended to preserve existing opcode numbers where possible. Changing the opcode width changes the binary encoding, so reassemble old programs.

Operand types:

| Type | Width | Meaning |
|---|---:|---|
| `r` | 4 bits | Register index, R0-R15 |
| `n` | 8 bits | Unsigned immediate, 0-255 |
| `i` | 8 bits | Signed immediate, -128 to 127 (two's complement) |

Operand formats are written in order. For example, `rrr` means three registers, `rnr` means register/immediate/register, and `rir` means register/signed-immediate/register.

## Instruction set

| Instruction | Format | Purpose |
|---|---|---|
| `MOV` | `rr` | Copy a register |
| `SET` | `rn` | Set a register to an unsigned immediate |
| `ADD`, `SUB`, `MUL`, `DIV` | `rrr` | Unsigned register arithmetic |
| `ADDI`, `SUBI`, `MULI`, `DIVI` | `rnr` | Unsigned arithmetic with an 8-bit immediate |
| `SADD`, `SSUB`, `SMUL`, `SDIV` | `rrr` | Signed register arithmetic |
| `SADDI`, `SSUBI`, `SMULI`, `SDIVI` | `rir` | Signed arithmetic with a signed 8-bit immediate |
| `AND`, `OR`, `XOR` | `rrr` | Bitwise register operations |
| `NOT` | `rr` | Bitwise complement |
| `ANDI`, `ORI`, `XORI` | `rnr` | Bitwise operations with an unsigned immediate |
| `SHL`, `SHR`, `SAR`, `ROL`, `ROR` | `rrr` | Shift or rotate by a count held in a register |
| `SHLI`, `SHRI`, `SARI`, `ROLI`, `RORI` | `rnr` | Shift or rotate by an immediate count |
| `PRINT` | `r` | Print a register value |
| `EVAL_ET`, `EVAL_NET` | `rr` | Equality / inequality comparison |
| `EVAL_LT`, `EVAL_GT`, `EVAL_LTE`, `EVAL_GTE` | `rr` | Relational comparison |
| `JMP`, `JMPIF` | `r` | Unconditional / conditional jump |
| `PUSHFLAG` | `r` | Copy the evaluation flag (0 or 1) to a register |
| `HALT` | empty | Stop the CPU |

All arithmetic results are stored as 64-bit register values. Unsigned arithmetic wraps modulo 2^64. Signed division truncates toward zero. Shift and rotate counts use the low 6 bits, so the effective count is 0-63. `SHR` is logical right shift; `SAR` is arithmetic right shift. Rotations operate on all 64 bits.

## Registers

- R0-R12 are general-purpose registers.
- R13 (RR) returns a new random 64-bit value when read.
- R14 (RTC) returns the current Unix timestamp in milliseconds.
- R15 (CLK) returns the CPU instruction tick count.

The special registers are read-only.

## Assembly example

```asm
SET R0 10
ADDI R0 5 R1
SUBI R1 3 R2
MULI R2 2 R3
DIVI R3 4 R4
SET R5 1
SHLI R5 4 R6
ROLI R6 4 R7
PRINT R1
PRINT R2
PRINT R3
PRINT R4
PRINT R7
HALT
```

Immediate values accept decimal or Python-style prefixed notation such as `0x10`. Signed immediates accept values from -128 through 127. Comments begin with a semicolon.

## Binary compatibility

The instruction size remains 24 bits, but the opcode field is now 6 bits instead of 5. Binaries assembled with the previous opcode layout are not compatible. Reassemble source files with the current `compile.py`.
