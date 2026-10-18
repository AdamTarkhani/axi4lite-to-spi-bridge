import cocotb
from cocotb.triggers import RisingEdge, ReadOnly
from cocotb.queue import Queue
from collections import namedtuple

WriteTransaction = namedtuple(
    "WriteTransaction", ["address", "data", "strb", "resp"]
)
ReadTransaction = namedtuple(
    "ReadTransaction", ["address", "data", "resp"]
)


class ProtocolError(Exception):
    """Raised on an AXI4-Lite protocol invariant violation (test plan
    section 1). Tests should let this propagate, not swallow it."""


class AxiLiteMonitor:
    def __init__(self, dut, clk):
        self.dut = dut
        self.clk = clk
        self.write_queue = Queue()
        self.read_queue = Queue()
        self._aw_pending = Queue()
        self._w_pending = Queue()
        self._running = False

    async def _watch_aw(self):
        while True:
            await ReadOnly()
            ready = self.dut.aw_ready.value == 1
            valid = self.dut.aw_valid.value == 1
            address = self.dut.aw_address.value
            await RisingEdge(self.dut.clk)
            if ready and valid:
                await self._aw_pending.put(int(address))
            elif not ready and valid:
                payload = await self._check_stable_until_ready(self.dut.aw_valid, self.dut.aw_ready, {"aw_address": self.dut.aw_address}, "AW_HANDSHAKE")
                await self._aw_pending.put(payload["aw_address"])
                await RisingEdge(self.dut.clk)
                
    async def _watch_w(self):
        while True:
            await ReadOnly()
            ready = self.dut.w_ready.value == 1
            valid = self.dut.w_valid.value == 1
            data = self.dut.w_data.value
            strb = self.dut.w_strb.value
            await RisingEdge(self.dut.clk)
            if ready and valid:
                await self._w_pending.put((int(data), int(strb)))
            elif not ready and valid:
                payload = await self._check_stable_until_ready(self.dut.w_valid, self.dut.w_ready, {"w_data": self.dut.w_data, "w_strb": self.dut.w_strb}, "W_HANDSHAKE")
                await self._w_pending.put((payload["w_data"], payload["w_strb"]))
                await RisingEdge(self.dut.clk)
                
    async def _watch_b(self):
        while True:
            await ReadOnly()
            ready = self.dut.b_ready.value == 1
            valid = self.dut.b_valid.value == 1
            b_resp = self.dut.b_resp.value
            await RisingEdge(self.dut.clk)
            if ready and valid:
                data, strb = await self._w_pending.get()
                address = await self._aw_pending.get()
                Write = WriteTransaction(address, data, strb, int(b_resp)) 
                await self.write_queue.put(Write)
            elif not ready and valid:
                payload = await self._check_stable_until_ready(self.dut.b_valid, self.dut.b_ready, {"b_resp": self.dut.b_resp},"B_RESP")
                data, strb = await self._w_pending.get()
                address = await self._aw_pending.get()
                Write = WriteTransaction(address, data, strb, int(payload["b_resp"])) 
                await self.write_queue.put(Write)
                await RisingEdge(self.dut.clk)
    
    async def _watch_ar_r(self):
        while True:
            await ReadOnly()
            ready_ar = self.dut.ar_ready.value == 1
            valid_ar = self.dut.ar_valid.value == 1
            ready_r = self.dut.r_ready.value == 1
            valid_r = self.dut.r_valid.value == 1
            await RisingEdge(self.dut.clk)
            if ready_ar and valid_ar:
                address = int(self.dut.ar_address.value)
            elif not ready_ar and valid_ar:
                payload = await self._check_stable_until_ready(self.dut.ar_valid, self.dut.ar_ready, {"ar_address": self.dut.ar_address}, "AR_HANDSHAKE")
                address = payload["ar_address"]
                await RisingEdge(self.dut.clk)
                continue
            if ready_r and valid_r:
                data = int(self.dut.r_data.value)
                r_resp = int(self.dut.r_resp.value)
                Read = ReadTransaction(address, data, r_resp)
                await self.read_queue.put(Read)
            elif not ready_r and valid_r:
                payload = await self._check_stable_until_ready(self.dut.r_valid, self.dut.r_ready, {"r_data": self.dut.r_data, "r_resp": self.dut.r_resp},"R_HANDSHAKE")
                Read = ReadTransaction(address, payload["r_data"], payload["r_resp"])
                await self.read_queue.put(Read)
                await RisingEdge(self.dut.clk)
    
    def start(self):
        """Spawn whatever watcher coroutine(s) you design.

        TODO: decide how many coroutines you need and what each one
        watches (a reasonable split is one per channel-pair -- AW, W, B,
        AR+R -- but that's a design choice, not a requirement), then
        start them with cocotb.start_soon(...).
        """
        
        if self._running:
            return
        self._running = True
        cocotb.start_soon(self._watch_aw())
        cocotb.start_soon(self._watch_w())
        cocotb.start_soon(self._watch_b())
        cocotb.start_soon(self._watch_ar_r())

    async def _check_stable_until_ready(self, valid_sig, ready_sig,
                                         payload_sigs, label):
        """Protocol invariant (test plan section 1): while valid is high
        and ready is low, none of payload_sigs may change, and valid
        must not be deasserted before ready fires. Raise ProtocolError
        with a useful message (include `label`) on either violation;
        return once ready is observed high.

        TODO: implement.
        """
        baseline = {name: sig.value for name, sig in payload_sigs.items()}
        while True:
            await ReadOnly()
            valid_stable = valid_sig.value == 1
            if not valid_stable:
                raise ProtocolError(
                    f"{label}: VALID deasserted before READY arrived "
                    f"(master abandoned a pending request)"
                    )
            for name, sigs in payload_sigs.items():
                current = sigs.value
                if current != baseline[name]:
                    raise ProtocolError(
                        f"{label}: '{name}' changed from {baseline[name]} to {current} "
                        f"while VALID was high and READY was low"
                        )
            if ready_sig.value == 1:
                return {name: int(sig.value) for name, sig in payload_sigs.items()}
            await RisingEdge(self.dut.clk)
   

    # ---- transaction capture ----
    #
    # TODO: implement the watcher coroutine(s) your start() spawns.
    # Each one needs to figure out when a handshake has actually
    # occurred on its channel(s) -- not just when valid happens to be
    # high, since valid can legitimately stay asserted across several
    # cycles -- capture the relevant address/data/response, and put it
    # on the appropriate queue:
    #   - AW captures go through self._aw_pending
    #   - W captures go through self._w_pending
    #   - once a B handshake completes, pair the oldest pending AW/W
    #     (see the single-outstanding note above) and push a
    #     WriteTransaction onto self.write_queue
    #   - AR + R together produce a ReadTransaction onto self.read_queue