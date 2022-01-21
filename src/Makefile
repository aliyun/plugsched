# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

# These files are disabled because they produce non-interesting flaky coverage
# that is not a function of syscall inputs. E.g. involuntary context switches.
KCOV_INSTRUMENT := n

ifneq ($(CONFIG_SCHED_OMIT_FRAME_POINTER),y)
# According to Alan Modra <alan@linuxcare.com.au>, the -fno-omit-frame-pointer is
# needed for x86 only.  Why this used to be enabled for all architectures is beyond
# me.  I suspect most platforms don't need this, but until we know that for sure
# I turn this off for IA-64 only.  Andreas Schwab says it's also needed on m68k
# to get a correct value for the wait-channel (WCHAN in ps). --davidm
CFLAGS_core.o := $(PROFILING) -fno-omit-frame-pointer
endif

OBJCOPYFLAGS := --globalize-symbols $(plugsched_tmpdir)/interface_fn_ptrs

objs-y += core.o
objs-y += idle.o fair.o rt.o deadline.o
objs-y += sidecar.o

objs-$(CONFIG_SMP) += cpupri.o cpudeadline.o topology.o stop_task.o pelt.o
objs-$(CONFIG_SCHEDSTATS) += stats.o
objs-$(CONFIG_SCHED_DEBUG) += debug.o

obj-m += scheduler.o

$(obj)/%.o: $(src)/%.c FORCE
	$(call cmd,force_checksrc)
	$(call if_changed_rule,cc_o_c)

$(obj)/%.stub.o: $(obj)/%.o FORCE
	$(call cmd,objcopy)

obj-stubs := $(patsubst %.o,%.stub.o,$(objs-y))

scheduler-objs := $(obj-stubs) main.o sched_rebuild.o

ldflags-y += -T $(plugsched_modpath)/scheduler.lds
ccflags-n += -DSCHEDMOD_MEMPOOL
