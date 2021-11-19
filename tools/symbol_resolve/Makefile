# SPDX-License-Identifier: GPL-2.0

ifeq ($(srctree),)
srctree := $(CURDIR)/../kernel
endif

include $(srctree)/tools/scripts/Makefile.include
include $(srctree)/tools/scripts/Makefile.arch

ifeq ($(ARCH),x86_64)
ARCH := x86
endif

# always use the host compiler
HOSTAR	?= ar
HOSTCC	?= gcc
HOSTLD	?= ld
AR	 = $(HOSTAR)
CC	 = $(HOSTCC)
LD	 = $(HOSTLD)

SYMBOL_RESOLVE    := $(OUTPUT)symbol_resolve

LIBELF_FLAGS := $(shell pkg-config --static libelf --cflags 2>/dev/null)
LIBELF_LIBS  := $(shell pkg-config --static libelf --libs 2>/dev/null || echo -lelf)

all: $(SYMBOL_RESOLVE)

INCLUDES := -I$(srctree)/tools/include \
	    -I$(srctree)/tools/arch/$(HOSTARCH)/include/uapi \
	    -I$(CURDIR)/arch/$(ARCH)/include

WARNINGS := $(EXTRA_WARNINGS)
CFLAGS   += -static -Werror $(WARNINGS) $(KBUILD_HOSTCFLAGS) -g $(INCLUDES) $(LIBELF_FLAGS)
LDFLAGS  += $(KBUILD_HOSTLDFLAGS)

# Allow old libelf to be used:
elfshdr := $(shell echo '$(pound)include <libelf.h>' | $(CC) $(CFLAGS) -x c -E - | grep elf_getshdr)
CFLAGS += $(if $(elfshdr),,-DLIBELF_USE_DEPRECATED)

OBJS := symbol_resolve.o

$(SYMBOL_RESOLVE): $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) $(OBJS) -o $(SYMBOL_RESOLVE) $(LIBELF_LIBS)

clean:
	$(call QUIET_CLEAN, symbol_resolve) $(RM) $(SYMBOL_RESOLVE)
	$(Q)find $(OUTPUT) -name '*.o' -delete

.PHONY: clean
