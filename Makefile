# Icarus Verilog. -g2012 because the RTL uses SystemVerilog constructs ($bits, int loop var).
# Run everything:        make

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
RTL ?= rtl/top_module.v

VERILOG_SOURCES = $(PWD)/$(RTL)
COMPILE_ARGS += -g2012

TOPLEVEL = top_module
COCOTB_TEST_MODULES ?= testbench

include $(shell cocotb-config --makefiles)/Makefile.sim
