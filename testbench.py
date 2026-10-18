"""Main testbench for the AXI4-Lite -> SPI bridge. This is the file `make` runs.

Helpers it uses:
  axi4lite_driver.py   drives the AXI4-Lite write and read channels
  axi4lite_monitor.py  watches the AXI channels, checks the protocol, queues finished transactions
  spi_agent.py         SPI slave (mode 0) that talks to the bridge's SPI pins
  scoreboard.py        reference model; checks responses, SPI words and RX_DATA
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly
from axi4lite_driver import AxiLiteWriteDriver, AxiLiteReadDriver, reset_dut
from axi4lite_monitor import AxiLiteMonitor
from spi_agent import SpiAgent
from scoreboard import Scoreboard, TX_DATA, RX_DATA, CONTROL, STATUS


# ---------------------------------------------------------------- helpers + basic transfers
async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.cpol.value = 0
    dut.cpha.value = 0
    dut.in_bit.value = 0
    w = AxiLiteWriteDriver(dut, dut.clk)
    r = AxiLiteReadDriver(dut, dut.clk)
    mon = AxiLiteMonitor(dut, dut.clk)
    agent = SpiAgent(dut)
    sb = Scoreboard(mon, agent)
    await reset_dut(dut, dut.clk)
    mon.start()
    cocotb.start_soon(agent.run())
    return w, r, agent, sb


async def settle(dut):
    """Let the monitor finish queueing the last transaction before the scoreboard
    drains it. Without this, a late read gets compared against a later transfer."""
    for _ in range(3):
        await RisingEdge(dut.clk)


async def wait_idle(dut, r, sb):
    """Poll STATUS over AXI until the bridge is idle (also exercises STATUS reads)."""
    for _ in range(500):
        data, _ = await r.read(STATUS)
        await settle(dut)
        sb.check_reads(busy_ok=True)
        if data == 0:
            return
    assert False, "bridge never went idle"


async def transfer(dut, w, r, sb, tx, rx_word, agent):
    agent.set_response(rx_word)
    await w.write_simultaneous(TX_DATA, tx)
    await w.write_simultaneous(CONTROL, 1)
    for _ in range(3000):
        await RisingEdge(dut.clk)
        if len(agent.transfers) > sb.checked and dut.cs.value == 1:
            break
    await wait_idle(dut, r, sb)
    await settle(dut)
    sb.check_all(busy_ok=True)


@cocotb.test()
async def single_transfer_and_readback(dut):
    w, r, agent, sb = await setup(dut)
    await transfer(dut, w, r, sb, 0xA5C3F00F, 0x3C5A96E1, agent)
    await r.read(RX_DATA)
    await settle(dut)
    sb.check_all()
    assert sb.checked == 1


@cocotb.test()
async def back_to_back_transfers(dut):
    w, r, agent, sb = await setup(dut)
    for tx, rx in [(0x00000001, 0x80000000), (0xFFFFFFFF, 0x00000000), (0x12345678, 0xDEADBEEF)]:
        await transfer(dut, w, r, sb, tx, rx, agent)
        await r.read(RX_DATA)
        await settle(dut)
        sb.check_all()
    assert sb.checked == 3


@cocotb.test()
async def byte_strobes(dut):
    w, r, agent, sb = await setup(dut)
    await w.write_simultaneous(TX_DATA, 0x11223344)
    await w.write_simultaneous(TX_DATA, 0xAABBCCDD, strb=0b0101)
    await w.write_strb_only(TX_DATA, 0b0000)
    await settle(dut)
    sb.check_all()
    agent.set_response(0x0)
    await w.write_simultaneous(CONTROL, 1)
    for _ in range(3000):
        await RisingEdge(dut.clk)
        if len(agent.transfers) and dut.cs.value == 1:
            break
    await wait_idle(dut, r, sb)
    await settle(dut)
    sb.check_all(busy_ok=True)
    assert agent.transfers[0].received == 0x11BB33DD, f"{agent.transfers[0].received:#010x}"


@cocotb.test()
async def illegal_accesses(dut):
    w, r, agent, sb = await setup(dut)
    await w.write_simultaneous(RX_DATA, 0x1)      # RO register
    await w.write_simultaneous(STATUS, 0x1)       # RO register
    await r.read(TX_DATA)                          # WO register
    await r.read(CONTROL)                          # WO register
    await r.read(0x2)                              # unmapped
    await settle(dut)
    sb.check_all()


# ---------------------------------------------------------------- SPI agent mode 0
@cocotb.test()
async def mode0_tx_matches_spec(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.cpol.value = 0
    dut.cpha.value = 0
    dut.in_bit.value = 0
    w = AxiLiteWriteDriver(dut, dut.clk)
    r = AxiLiteReadDriver(dut, dut.clk)
    await reset_dut(dut, dut.clk)

    agent = SpiAgent(dut)
    agent.set_response(0x3C5A96E1)
    cocotb.start_soon(agent.run())

    sent = 0xA5C3F00F
    await w.write_simultaneous(0x0, sent)
    await w.write_simultaneous(0x8, 1)
    for _ in range(3000):
        await RisingEdge(dut.clk)
        if agent.transfers:
            break
    assert agent.transfers, "no SPI transfer observed"
    got = agent.transfers[0].received
    assert got == sent, f"TX mismatch: master sent {sent:#010x}, agent saw {got:#010x}"

    data, resp = await r.read(0x4)
    assert data == 0x3C5A96E1, f"RX mismatch: expected 0x3C5A96E1, RX_DATA={data:#010x}"


# ---------------------------------------------------------------- write ordering and backpressure
async def start_transfer(dut, w, sb, agent, write_fn, tx, rx_word, **kwargs):
    """Write TX_DATA and CONTROL with the given write ordering, then wait for the transfer to end."""
    agent.set_response(rx_word)
    await write_fn(TX_DATA, tx, **kwargs)
    await write_fn(CONTROL, 1, **kwargs)
    count = len(agent.transfers)
    for _ in range(3000):
        await RisingEdge(dut.clk)
        if len(agent.transfers) > count:
            break


@cocotb.test()
async def aw_first(dut):
    w, r, agent, sb = await setup(dut)
    await start_transfer(dut, w, sb, agent, w.write_aw_first, 0xCAFEF00D, 0x11112222, gap_cycles=3)
    await wait_idle(dut, r, sb)
    await r.read(RX_DATA)
    await settle(dut)
    sb.check_all()


@cocotb.test()
async def w_first(dut):
    w, r, agent, sb = await setup(dut)
    await start_transfer(dut, w, sb, agent, w.write_w_first, 0xCAFEF00D, 0x33334444, gap_cycles=3)
    await wait_idle(dut, r, sb)
    await r.read(RX_DATA)
    await settle(dut)
    sb.check_all()


@cocotb.test()
async def simultaneous(dut):
    w, r, agent, sb = await setup(dut)
    await start_transfer(dut, w, sb, agent, w.write_simultaneous, 0xCAFEF00D, 0x55556666)
    await wait_idle(dut, r, sb)
    await r.read(RX_DATA)
    await settle(dut)
    sb.check_all()


@cocotb.test()
async def slow_b_ready_and_r_ready(dut):
    w, r, agent, sb = await setup(dut)
    await start_transfer(dut, w, sb, agent, w.write_simultaneous, 0x0F0F0F0F, 0x77778888, ready_delay=5)
    await wait_idle(dut, r, sb)
    await r.read(RX_DATA, ready_delay=5)
    await settle(dut)
    sb.check_all()


@cocotb.test()
async def mixed_orderings_in_a_row(dut):
    w, r, agent, sb = await setup(dut)
    for write_fn, tx, rx in [(w.write_aw_first, 0x1, 0xA), (w.write_w_first, 0x2, 0xB), (w.write_simultaneous, 0x3, 0xC)]:
        await start_transfer(dut, w, sb, agent, write_fn, tx, rx)
        await wait_idle(dut, r, sb)
        await r.read(RX_DATA)
        await settle(dut)
        sb.check_all()
    assert sb.checked == 3


# ---------------------------------------------------------------- irq
@cocotb.test()
async def irq_pulses_once_per_transfer(dut):
    w, r, agent, sb = await setup(dut)
    agent.set_response(0x12345678)
    await w.write_simultaneous(TX_DATA, 0xA5C3F00F)
    await w.write_simultaneous(CONTROL, 1)

    irq_count = 0           # clock cycles where irq was high
    irq_while_busy = 0      # of those, how many while cs was still low (transfer running)
    cycles_after_end = 0    # cycles we watched after the transfer finished
    while cycles_after_end < 50:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if dut.irq.value == 1:
            irq_count += 1
            if dut.cs.value == 0:
                irq_while_busy += 1
        if len(agent.transfers) > 0:
            cycles_after_end += 1

    assert irq_while_busy == 0, "irq fired while the transfer was still running"
    assert irq_count == 1, f"expected exactly 1 irq cycle for 1 transfer, saw {irq_count}"


@cocotb.test()
async def irq_stays_low_when_idle(dut):
    w, r, agent, sb = await setup(dut)
    for _ in range(100):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert dut.irq.value == 0, "irq high with no transfer started"
