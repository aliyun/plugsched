# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

SYMBOL_RESOLVE    := $(OUTPUT)symbol_resolve
LIBELF_FLAGS := $(shell pkg-config --static libelf --cflags 2>/dev/null)
LIBELF_LIBS  := $(shell pkg-config --static libelf --libs 2>/dev/null || echo -lelf)

all: $(SYMBOL_RESOLVE)

CPP = g++
CPPFLAGS = -g -static
OBJS := symbol_resolve.o

$(SYMBOL_RESOLVE): $(OBJS)
	$(CPP) $(CPPFLAGS) $(LDFLAGS) $(OBJS) -o $(SYMBOL_RESOLVE) $(LIBELF_LIBS)

clean:
	rm -f symbol_resolve symbol_resolve.o

.PHONY: clean
