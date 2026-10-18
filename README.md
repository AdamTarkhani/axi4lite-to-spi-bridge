# AXI4-Lite to SPI Bridge

An AXI4-Lite slave that drives an SPI master, written in Verilog, with a cocotb verification environment.
The README has two parts: [the RTL](#part-1-the-rtl) and [the verification](#part-2-verification).

All simulation was done on EDA Playground (cocotb + Icarus Verilog); the files were transferred here from there.

---

## Part 1: The RTL

### What it is and what it is for

A processor or SoC usually talks to its peripherals through a memory-mapped bus such as AXI4-Lite. Many real
peripherals (flash chips, sensors, ADCs, displays) only speak SPI. This design sits between the two: software
reads and writes four registers over AXI4-Lite, and the bridge turns those into 32-bit SPI transfers on the pins.

```
 AXI4-Lite master  <--->  AXI write slave / AXI read slave  <--->  SPI master  <--->  SPI device
 (CPU / testbench)         (4 registers, responses)                (sclk, cs, out_bit, in_bit)
```

### Modules (all in `rtl/top_module.v`)

| Module | Job |
|---|---|
| `top_module` | Wires everything together. Parameters `CLK_FRQ` and `N` set the SPI clock rate. |
| `reset_sync` | Two-flop synchronizer: reset asserts immediately, releases synchronously to `clk`. |
| `AXI_write_slave` | AW, W and B channels. Latches TX_DATA and CONTROL writes. |
| `AXI_read_slave` | AR and R channels. Returns RX_DATA and STATUS. |
| `SPI_master` | Shifts 32 bits out and in, with CPOL/CPHA inputs, a busy status and an IRQ pulse. |

### Register map (32-bit data, 4-byte-aligned offsets)

| Offset | Name | Access | Meaning |
|---|---|---|---|
| `0x0` | TX_DATA | write | The 32-bit word to send. Byte strobes (`w_strb`) select which bytes are updated. |
| `0x4` | RX_DATA | read | The 32-bit word received during the last finished transfer. |
| `0x8` | CONTROL | write | Bit 0 = start a transfer. Clears itself once the transfer is running. |
| `0xC` | STATUS | read | Bit 0 = busy. |

Any other address, a write to a read-only register, or a read of a write-only register returns an
AXI `SLVERR` response.

### Protections and design decisions

- **Synchronized reset.** `aresetn` can assert at any time, but is released through a two-flop synchronizer
  (`reset_sync`), which avoids reset-removal timing hazards. Every internal module resets on the synchronized version.
- **Two-flop input synchronizer** on the SPI `in_bit` pin, since it arrives from outside the clock domain.
- **`SLVERR` for bad accesses**, on both the write and read slaves.
- **RX_DATA read protection.** Reading RX_DATA while a transfer is running returns `SLVERR` and 0, because the
  register is only half-shifted at that point.
- **AW and W in any order.** The write slave accepts the address and the data on the same cycle or on different
  cycles, and holds whichever arrives first until the other shows up.
- **Byte strobes** on TX_DATA writes.
- **One outstanding transaction** per direction. `aw_ready`/`w_ready` stay low until the response has been taken.
- **Self-clearing CONTROL** start bit.
- **Unused `prot` signals** (`aw_prot`, `ar_prot`). There is no privilege or security gating on this peripheral.
- **IRQ** is a single-cycle pulse when a transfer finishes.

### The protocols

**AXI4-Lite** is the simple, no-burst version of AXI. It has five independent channels, and each one moves
information with a VALID/READY handshake:

| Channel | Direction | Carries |
|---|---|---|
| AW (write address) | master to slave | where to write |
| W (write data) | master to slave | the data and byte strobes |
| B (write response) | slave to master | OKAY or error |
| AR (read address) | master to slave | where to read |
| R (read data) | slave to master | the data and OKAY or error |

A transfer happens on the clock edge where VALID and READY are both high. A sender must keep VALID high and
its payload stable until READY arrives, and READY is allowed to go high before VALID.

**SPI** is a four-wire synchronous serial bus. The master drives the clock (`sclk`) and chip select (`cs`, active
low) and sends bits on one data line while receiving on another (`out_bit`, `in_bit` here), so it is full duplex:
every bit sent is matched by a bit received. CPOL sets the idle level of `sclk`, and CPHA sets whether data is
sampled on the first or second clock edge, which gives four modes. This bridge sends 32 bits per transfer,
MSB first.

### Future plans

- Verify the other three SPI modes (CPOL/CPHA 01, 10 and 11) and fix the RTL where needed.
- Decide and document the behavior for transactions during the reset-release window, and for CONTROL values
  with bits other than bit 0 set.
- Randomized stimulus and functional coverage on top of the directed tests.
- Check that the design synthesizes cleanly for an FPGA.

---

## Part 2: Verification

### Setup

A cocotb testbench (Python) with the usual driver / monitor / scoreboard structure, plus an SPI agent for the
other side of the bridge. Simulator: Icarus Verilog 12 with cocotb 2.1 (`-g2012`).

| File | What it is |
|---|---|
| `testbench.py` | The main testbench. This is the file `make` runs. |
| `axi4lite_driver.py` | Drives the AXI4-Lite write and read channels. Has AW-first, W-first and simultaneous write orderings and configurable ready delays. |
| `axi4lite_monitor.py` | Watches the AXI channels without driving them. Checks that VALID stays high and the payload stays stable until READY, and queues every finished write and read. |
| `spi_agent.py` | A mode-0 SPI slave. It uses only the SPI pins (`cs`, `sclk`, `out_bit`, `in_bit`), written to the SPI protocol and not to the RTL. |
| `scoreboard.py` | A reference model of the register map. It checks AXI responses, the words seen on the SPI bus, and RX_DATA reads. |

### Running it

    make        # runs testbench.py (12 tests) against rtl/top_module.v

You need Python 3.9+, cocotb 2.1, Icarus Verilog and make.

### What is covered (all 12 tests pass)

- Single transfer with RX_DATA readback, and several transfers back to back.
- Byte strobes on TX_DATA, including partial strobes.
- Illegal accesses: writes to read-only registers and reads of write-only or unmapped registers must return `SLVERR`.
- Write ordering: AW first, W first, both on the same cycle, and three different orderings in a row.
- Slow `b_ready` and `r_ready` (backpressure on the response channels).
- IRQ: exactly one pulse per transfer, never while the transfer is running, never while idle.
- SPI mode 0: the master's transmitted word must match the written TX_DATA exactly, and the received word must match what the agent sent.

### Still to be checked

- **CPOL/CPHA sweep.** The SPI agent is mode 0 only. The other three modes need the agent to pick its sample and
  drive edges from CPOL/CPHA. Only mode 0 has been verified so far, and the RTL may need changes for the others.
- **Reset checks.** Reset in the middle of a transfer (the bridge should return to idle, with `cs` high, `sclk`
  at its CPOL level, no IRQ and STATUS/RX_DATA at 0, and then work normally), and a transaction arriving during
  the two-cycle reset-release window.
- **Open spec questions:** what CONTROL should do with a write like `0x3` (the RTL only looks at bit 0), and the
  exact IRQ cycle relative to `cs` rising (the tests only require one pulse that is not early).
- **Not covered:** randomized stimulus, functional coverage, and the `control_reg` self-clear when CONTROL is
  written while the bridge is still busy.
- **Out of scope for cocotb:** metastability of the reset and input synchronizers. A behavioral simulation only
  shows the fixed two-cycle delay. That is a static-timing and CDC-checker question.
