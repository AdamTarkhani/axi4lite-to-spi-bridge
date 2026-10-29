module top_module #(
    parameter CLK_FRQ = 100,
    parameter N       = 4
) (
    input               clk,
    input               aresetn,

    // AXI4-Lite write address/data/response
    input  [3:0]        aw_address,
    input               aw_valid,
    output              aw_ready,
    input               w_valid,
    input  [31:0]       w_data,
    input  [3:0]        w_strb,
    output              w_ready,
    input  [2:0]        aw_prot,
    output              b_valid,
    input               b_ready,
    output [1:0]        b_resp,

    // AXI4-Lite read address/data/response
    input  [3:0]        ar_address,
    input               ar_valid,
    output              ar_ready,
    input  [2:0]        ar_prot,
    output              r_valid,
    input               r_ready,
    output [31:0]       r_data,
    output [1:0]        r_resp,

    // SPI
    input               cpha,
    input               cpol,
    output              sclk,
    output              cs,
    output              out_bit,
    input               in_bit,
    output              irq
);

    wire        control, status;
    wire [31:0] tx_data, rx_data;

    // Synchronized reset: aresetn is the raw async pin-level reset (async
    // assert). reset_out is the version released synchronously to clk via
    // a 2-flop synchronizer, and is what every internal module actually
    // resets on. This avoids reset-recovery/removal timing hazards if
    // aresetn deasserts close to a clock edge.
    wire reset_out;

    reset_sync u_reset_sync (
        .clk      (clk),
        .aresetn  (aresetn),
        .reset_out(reset_out)
    );

    AXI_write_slave u_axi_write (
        .clk        (clk),
        .aresetn    (reset_out),
        .aw_address (aw_address),
        .aw_valid   (aw_valid),
        .aw_ready   (aw_ready),
        .aw_prot    (aw_prot),
        .w_valid    (w_valid),
        .w_data     (w_data),
        .w_strb     (w_strb),
        .w_ready    (w_ready),
        .b_valid    (b_valid),
        .b_ready    (b_ready),
        .b_resp     (b_resp),
        .control    (control),
        .status     (status),
        .tx_data    (tx_data)
    );

    AXI_read_slave u_axi_read (
        .clk        (clk),
        .aresetn    (reset_out),
        .ar_address (ar_address),
        .ar_valid   (ar_valid),
        .ar_ready   (ar_ready),
        .ar_prot    (ar_prot),
        .r_valid    (r_valid),
        .r_ready    (r_ready),
        .r_data     (r_data),
        .r_resp     (r_resp),
        .status     (status),
        .rx_data    (rx_data)
    );

    SPI_master #(
        .CLK_FRQ (CLK_FRQ),
        .N       (N)
    ) u_spi (
        .clk      (clk),
        .aresetn  (reset_out),
        .control  (control),
        .status   (status),
        .tx_data  (tx_data),
        .rx_data  (rx_data),
        .cpha     (cpha),
        .cpol     (cpol),
        .sclk     (sclk),
        .cs       (cs),
        .out_bit  (out_bit),
        .in_bit   (in_bit),
        .irq      (irq)
    );

endmodule


module reset_sync (
    input  clk,
    input  aresetn,
    output reset_out
);

    reg sync_reg1;
    reg sync_reg2;

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            sync_reg1 <= 1'b0;
            sync_reg2 <= 1'b0;
        end else begin
            sync_reg1 <= 1'b1;
            sync_reg2 <= sync_reg1;
        end
    end

    assign reset_out = sync_reg2;

endmodule


module AXI_write_slave (
    input               clk,
    input               aresetn,

    input  [3:0]        aw_address,
    input               aw_valid,
    output              aw_ready,
    input  [2:0]        aw_prot,   // AXI4-Lite protection signal; unused — no privilege/security gating on this peripheral

    input               w_valid,
    input  [31:0]       w_data,
    input  [3:0]        w_strb,
    output              w_ready,

    output              b_valid,
    input               b_ready,
    output reg [1:0]    b_resp,

    output              control,
    input               status,
    output [31:0]       tx_data
);

    localparam IDLE = 0, RESPONSE = 1;
    localparam TX_DATA_ADDRESS = 4'h0, CONTROL_ADDRESS = 4'h8;

    reg        state, next_state;
    reg [3:0]  aw_reg_address;
    reg [31:0] tx_reg_data, w_reg_data;
    reg        control_reg;
    reg        w_done, aw_done, b_trigger;

    wire write_enabled = b_ready & b_valid;
    wire trigger_con = ((w_valid & w_ready) || w_done) && ((aw_valid & aw_ready) || aw_done);

    // Next-state logic
    always @(*) begin
        case (state)
            IDLE: next_state = trigger_con ? RESPONSE : IDLE;
            RESPONSE: next_state = write_enabled ? IDLE : RESPONSE;
            default: next_state = IDLE;
        endcase
    end

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) state <= IDLE;
        else state <= next_state;
    end

    // Address/data capture, register writes, response
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            aw_reg_address <= 4'b0;
            aw_done <= 1'b0;
            w_reg_data <= 32'b0;
            tx_reg_data <= 32'b0;
            control_reg <= 1'b0;
            w_done <= 1'b0;
            b_trigger <= 1'b0;
            b_resp <= 2'b00;
        end else begin

            if (w_valid & w_ready) begin
                w_reg_data <= w_data;
                w_done <= 1'b1;
            end

            if (aw_valid & aw_ready) begin
                aw_reg_address <= aw_address;
                aw_done <= 1'b1;
            end

            if (trigger_con && !b_trigger) begin
                b_trigger <= 1'b1;
                case ((aw_valid & aw_ready) ? aw_address : aw_reg_address)
                    TX_DATA_ADDRESS: begin
                        for (int i = 0; i < $bits(w_strb); i++) begin
                            if (w_strb[i]) begin
                                tx_reg_data[8*i +: 8] <= (w_valid & w_ready) ? w_data[8*i +: 8] : w_reg_data[8*i +: 8];
                            end
                        end
                        b_resp <= 2'b00;
                    end
                    CONTROL_ADDRESS: begin
                        if (w_strb[0])
                            control_reg <= (w_valid & w_ready) ? w_data[0] : w_reg_data[0];
                        b_resp <= 2'b00;
                    end
                    default: b_resp <= 2'b10;
                endcase
            end

            if (write_enabled) begin
                w_done <= 1'b0;
                aw_done <= 1'b0;
                b_trigger <= 1'b0;
            end

            if (control_reg && status) begin
                control_reg <= 1'b0;
            end
        end
    end

    assign b_valid = (state == RESPONSE);
    assign aw_ready = ~aw_done;
    assign w_ready = ~w_done;
    assign control = control_reg;
    assign tx_data = tx_reg_data;

endmodule


module AXI_read_slave (
    input               clk,
    input               aresetn,

    input  [3:0]        ar_address,
    input               ar_valid,
    output              ar_ready,
    input  [2:0]        ar_prot,   // AXI4-Lite protection signal; unused — no privilege/security gating on this peripheral

    output              r_valid,
    input               r_ready,
    output reg [31:0]   r_data,
    output reg [1:0]    r_resp,

    input               status,
    input  [31:0]       rx_data
);

    localparam IDLE = 0, RESPONSE = 1;
    localparam RX_DATA_ADDRESS = 4'h4, STATUS_ADDRESS = 4'hC;

    reg state, next_state;

    always @(*) begin
        case (state)
            IDLE: next_state = (ar_valid & ar_ready) ? RESPONSE : IDLE;
            RESPONSE: next_state = (r_valid & r_ready) ? IDLE : RESPONSE;
        endcase
    end

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) state <= IDLE;
        else state <= next_state;
    end

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            r_resp <= 2'b00;
            r_data <= 32'b0;
        end else begin
            if (ar_valid & ar_ready) begin
                case (ar_address)
                    RX_DATA_ADDRESS: begin
                        if (status) begin
                            r_data <= 32'b0;
                            r_resp <= 2'b10;
                        end else begin
                            r_data <= rx_data;
                            r_resp <= 2'b00;
                        end
                    end
                    STATUS_ADDRESS: begin
                        r_data <= {31'b0, status};
                        r_resp <= 2'b00;
                    end
                    default: begin
                        r_data <= 32'b0;
                        r_resp <= 2'b10;
                    end
                endcase
            end
        end
    end

    assign r_valid = (state == RESPONSE);
    assign ar_ready = (state == IDLE);

endmodule

//FIX THE SPI MASTER
module SPI_master #(
    parameter CLK_FRQ,
    parameter N
) (
    input               clk,
    input               aresetn,

    input               control,
    output              status,
    input  [31:0]       tx_data,
    output [31:0]       rx_data,

    input               cpha,
    input               cpol,
    output reg          sclk,
    output              cs,
    output reg          out_bit,
    input               in_bit,
    output              irq
);

    localparam IDLE = 0, TRANSFER = 1;   // states for bit transfer
    localparam FREE = 0, BUSY = 1;       // states for status

    localparam [9:0] divided_period = CLK_FRQ / N;

    reg        state, next_state;
    reg [31:0] tx_reg_data, rx_reg_data;
    reg [5:0]  bit_counter;
    reg [12:0] counter;
    reg        next_in_bit1, next_in_bit2;
    reg        reg_irq;

    wire tick = (counter == divided_period - 1);
    wire edge_tick = tick;

    // Transfer state machine
    always @(*) begin
        case (state)
            IDLE: next_state = control ? TRANSFER : IDLE;
            TRANSFER: next_state = (bit_counter == 63) ? IDLE : TRANSFER;
        endcase
    end

    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) state <= IDLE;
        else state <= next_state;
    end

    // Baud-rate tick counter
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            counter <= 13'b0;
        end else if (tick || state != TRANSFER) begin
            counter <= 13'b0;
        end else begin
            counter <= counter + 1'd1;
        end
    end

    // Bit/edge counter (2 edges per bit, 32 bits -> 64 edges)
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            bit_counter <= 6'b0;
        end else if (state != TRANSFER) begin
            bit_counter <= 6'b0;
        end else if (tick) begin
            bit_counter <= bit_counter + 1'b1;
        end
    end

    // SCLK generation (CPOL sets idle level)
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            sclk <= cpol;
        end else if (state == IDLE) begin
            sclk <= cpol;
        end else if (tick) begin
            sclk <= ~sclk;
        end
    end

    // Shift-out / shift-in, per CPHA
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            tx_reg_data <= 32'b0;
            rx_reg_data <= 32'b0;
            out_bit <= 1'b0;
        end else begin
            if (state == IDLE && control) begin
                tx_reg_data <= tx_data;
                out_bit <= tx_data[31];
            end
            if (edge_tick) begin
                case (state)
                    TRANSFER: begin
                        if ((sclk && ~cpha) || (~sclk && cpha)) begin
                            out_bit <= tx_reg_data[30 - bit_counter[5:1]];
                        end else if ((~sclk && ~cpha) || (sclk && cpha)) begin
                            rx_reg_data <= {rx_reg_data[30:0], next_in_bit2};
                        end
                    end
                    IDLE: out_bit <= 1'b0;
                endcase
            end
        end
    end

    // Two-stage synchronizer for in_bit
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) begin
            next_in_bit1 <= 1'b0;
            next_in_bit2 <= 1'b0;
        end else begin
            next_in_bit1 <= in_bit;        // first stage — may go metastable
            next_in_bit2 <= next_in_bit1;  // second stage — gives stage 1 time to resolve
        end
    end

    // IRQ pulse on transfer completion
    always @(posedge clk or negedge aresetn) begin
        if (!aresetn) reg_irq <= 1'b0;
        else reg_irq <= (state == TRANSFER) && (next_state == IDLE);
    end

    assign status = (state == TRANSFER) ? BUSY : FREE;
    assign cs = (state != TRANSFER);
    assign rx_data = rx_reg_data;
    assign irq = reg_irq;

endmodule
