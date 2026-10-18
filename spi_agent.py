import cocotb
from collections import namedtuple
from cocotb.triggers import RisingEdge, FallingEdge, ReadOnly

SpiTransfer = namedtuple("SpiTransfer", ["received", "sent"])


class SpiAgent:
    """SPI slave, mode 0 (CPOL=0, CPHA=0), written to the SPI spec, not to the RTL.

    Per transfer: cs falls -> drive MSB of tx_word on in_bit -> on each rising
    sclk sample out_bit (MSB first) -> on each falling sclk drive the next
    in_bit bit -> cs rises -> record (received, sent).
    Uses only SPI-visible pins (cs, sclk, out_bit, in_bit).
    """

    def __init__(self, dut):
        self.dut = dut
        self.dut.in_bit.value = 0
        self.tx_word = 0
        self.transfers = []

    def set_response(self, word):
        self.tx_word = word

    async def run(self):
        while True:
            await FallingEdge(self.dut.cs)
            sent = self.tx_word
            self.dut.in_bit.value = (sent >> 31) & 1

            received = 0
            for i in range(32):
                await RisingEdge(self.dut.sclk)
                await ReadOnly()
                received = (received << 1) | int(self.dut.out_bit.value)

                if i == 31:
                    break
                await FallingEdge(self.dut.sclk)
                self.dut.in_bit.value = (sent >> (30 - i)) & 1

            await RisingEdge(self.dut.cs)
            self.transfers.append(SpiTransfer(received, sent))
