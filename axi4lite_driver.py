import cocotb
from cocotb.triggers import RisingEdge, ReadOnly


class AxiLiteWriteDriver:
    """Drives the AW, W, and B channels."""

    def __init__(self, dut, clk):
        self.dut = dut
        self.clk = clk
        self.idle()

    def idle(self):
        """Deassert everything this driver owns. Call once before reset
        release, and anywhere you need to force the bus back to idle."""
        self.dut.aw_valid.value = 0
        self.dut.aw_address.value = 0
        self.dut.aw_prot.value = 0
        self.dut.w_valid.value = 0
        self.dut.w_data.value = 0
        self.dut.w_strb.value = 0
        self.dut.b_ready.value = 0

    async def _aw_only(self, address, prot=0):
        """Assert AW with the given address/prot, hold until the DUT
        accepts it (aw_valid & aw_ready both true at the same rising
        edge), then deassert aw_valid. Must not touch the W or B
        channels -- this is a building block for the ordering-case
        methods below, which combine AW-only and W-only independently.

        TODO: implement.
        """
        self.dut.aw_address.value = address
        self.dut.aw_prot.value = prot
        self.dut.aw_valid.value = 1
        
        while True:
            await ReadOnly()
            ready = self.dut.aw_ready.value == 1
            await RisingEdge(self.dut.clk)
            if ready:
                break
        
        self.dut.aw_valid.value = 0

    async def _w_only(self, data, strb=0xF):
        """Mirror of _aw_only for the W channel.

        TODO: implement.
        """
        self.dut.w_data.value = data
        self.dut.w_strb.value = strb
        self.dut.w_valid.value = 1
        
        while True:
            await ReadOnly()
            ready = self.dut.w_ready.value == 1
            await RisingEdge(self.dut.clk)
            if ready:
                break
        
        self.dut.w_valid.value = 0

    async def _b_handshake(self, ready_delay=0):
        """Wait for b_valid, then assert b_ready to accept the response
        -- waiting ready_delay extra idle cycles first if nonzero, for
        the eventual backpressure tests (test plan section 4) -- and
        return b_resp as an int (0b00 = OKAY, 0b10 = SLVERR).

        TODO: implement.
        """
        while True:
            await ReadOnly()
            valid = self.dut.b_valid.value == 1
            await RisingEdge(self.dut.clk)
            if valid:
                break
        
        for _ in range(ready_delay):
            await RisingEdge(self.dut.clk)
        
        self.dut.b_ready.value = 1
        await ReadOnly()
        b_resp = int(self.dut.b_resp.value)
        await RisingEdge(self.dut.clk)
        self.dut.b_ready.value = 0
        return b_resp
        
    # ---- ordering cases (test plan section 1) ----

    async def write_aw_first(self, address, data, strb=0xF, gap_cycles=2,
                              ready_delay=0):
        """AW handshake completes, gap_cycles idle cycles pass, then the
        W handshake completes, then wait for the B response.

        TODO: implement using _aw_only / _w_only / _b_handshake.
        """
        await self._aw_only(address, 0)
        for _ in range(gap_cycles):
            await RisingEdge(self.dut.clk)
        await self._w_only(data, strb)
        b_resp = await self._b_handshake(ready_delay)
        return b_resp

    async def write_w_first(self, address, data, strb=0xF, gap_cycles=2,
                             ready_delay=0):
        """Mirror of write_aw_first: W handshake completes first.

        TODO: implement.
        """
        await self._w_only(data, strb)
        for _ in range(gap_cycles):
            await RisingEdge(self.dut.clk)
        await self._aw_only(address, 0)
        b_resp = await self._b_handshake(ready_delay)
        return b_resp

    async def write_simultaneous(self, address, data, strb=0xF,
                                  ready_delay=0):
        """AW and W both asserted starting the same cycle.

        Note: AXI4-Lite does not guarantee aw_ready and w_ready resolve
        on the same cycle as each other in general -- check how THIS
        DUT's aw_ready/w_ready are actually generated in top_module.v
        and decide for yourself whether assuming same-cycle resolution
        is safe here, or whether your implementation needs to handle
        them independently regardless.

        TODO: implement.
        """
        aw_task = cocotb.start_soon(self._aw_only(address, 0))
        w_task = cocotb.start_soon(self._w_only(data, strb))
        
        await aw_task
        await w_task
        
        b_resp = await self._b_handshake(ready_delay)
        return b_resp

    async def write_strb_only(self, address, strb):
        """Convenience for the w_strb=4'b0000 edge case (test plan
        section 3): a write with an all-zero strobe and don't-care data.

        TODO: implement (likely just calls write_simultaneous with a
        placeholder data value).
        """
        placeholder = 0xFFFFFFFF
        return await self.write_simultaneous(address, placeholder, strb,)


class AxiLiteReadDriver:
    """Drives the AR and R channels."""

    def __init__(self, dut, clk):
        self.dut = dut
        self.clk = clk
        self.idle()

    def idle(self):
        self.dut.ar_valid.value = 0
        self.dut.ar_address.value = 0
        self.dut.ar_prot.value = 0
        self.dut.r_ready.value = 0

    async def read(self, address, prot=0, ready_delay=0):
        """Issue a read: AR handshake, then wait for the R response
        (waiting ready_delay extra cycles before asserting r_ready if
        nonzero), return (data, resp).

        TODO: implement.
        """
        self.dut.ar_address.value = address
        self.dut.ar_prot.value = prot
        self.dut.ar_valid.value = 1;
        
        while True:
            await ReadOnly()
            ready = self.dut.ar_ready.value == 1
            await RisingEdge(self.dut.clk)
            if (ready):
                break
                
        self.dut.ar_valid.value = 0
        
        for _ in range(ready_delay):
            await RisingEdge(self.dut.clk)
        
        self.dut.r_ready.value = 1
        
        while True:
            await ReadOnly()
            valid = self.dut.r_valid.value == 1
            await RisingEdge(self.dut.clk)
            if valid:
                break
        
        
        self.dut.r_ready.value = 0
        return int(self.dut.r_data.value), int(self.dut.r_resp.value)

async def reset_dut(dut, clk, cycles=5):
    """Standard async-assert / sync-release reset sequence: drive
    aresetn low for `cycles` clock cycles, then release it.

    TODO: implement.
    """
    dut.aresetn.value = 0;
    
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    
    dut.aresetn.value = 1
    
    for _ in range(2):
        await RisingEdge(dut.clk)
    
    await ReadOnly()
    assert dut.reset_out.value == 1, f" async-assert / sync-release reset sequence failed"
    await RisingEdge(dut.clk)
