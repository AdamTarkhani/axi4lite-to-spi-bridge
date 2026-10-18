OKAY = 0
SLVERR = 2

TX_DATA = 0x0
RX_DATA = 0x4
CONTROL = 0x8
STATUS = 0xC


class Scoreboard:
    def __init__(self, monitor, agent):
        self.monitor = monitor
        self.agent = agent
        self.tx_bytes = [0, 0, 0, 0]
        self.expected_tx = []
        self.checked = 0

    def check_writes(self):
        while not self.monitor.write_queue.empty():
            wr = self.monitor.write_queue.get_nowait()

            strobe_text = format(wr.strb, "04b")   # strb 5 -> "0101", last character is byte 0

            if wr.address == TX_DATA:
                expected = OKAY
                new_bytes = wr.data.to_bytes(4, "little")
                for i in range(4):
                    if strobe_text[3 - i] == "1":
                        self.tx_bytes[i] = new_bytes[i]
            elif wr.address == CONTROL:
                expected = OKAY
                if strobe_text[3] == "1" and wr.data % 2 == 1:
                    self.expected_tx.append(int.from_bytes(bytes(self.tx_bytes), "little"))
            else:
                expected = SLVERR

            assert wr.resp == expected, f"write {hex(wr.address)}: resp {wr.resp}, expected {expected}"

    def check_spi(self):
        while self.checked < len(self.agent.transfers):
            assert self.checked < len(self.expected_tx), "SPI transfer with no start written"
            got = self.agent.transfers[self.checked].received
            expected = self.expected_tx[self.checked]
            assert got == expected, f"SPI tx: expected {hex(expected)}, got {hex(got)}"
            self.checked += 1

    def check_reads(self, busy_ok=False):
        while not self.monitor.read_queue.empty():
            rd = self.monitor.read_queue.get_nowait()

            if rd.address == RX_DATA:
                if rd.resp == SLVERR:
                    assert busy_ok, "RX_DATA gave SLVERR but bridge should be idle"
                    assert rd.data == 0, "RX_DATA SLVERR should return 0"
                else:
                    last_rx = 0
                    if len(self.agent.transfers) > 0:
                        last_rx = self.agent.transfers[-1].sent
                    assert rd.data == last_rx, f"RX_DATA: expected {hex(last_rx)}, got {hex(rd.data)}"
            elif rd.address == STATUS:
                assert rd.resp == OKAY, "STATUS should be OKAY"
                assert rd.data == 0 or rd.data == 1, f"STATUS should be 0 or 1, got {hex(rd.data)}"
            else:
                assert rd.resp == SLVERR, f"read {hex(rd.address)}: expected SLVERR"
                assert rd.data == 0, f"read {hex(rd.address)}: SLVERR should return 0"

    def check_all(self, busy_ok=False):
        self.check_writes()
        self.check_spi()
        self.check_reads(busy_ok)
