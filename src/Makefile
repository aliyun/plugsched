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

objs-y += core.o
objs-y += idle.o fair.o rt.o deadline.o

objs-$(CONFIG_SMP) += cpupri.o cpudeadline.o topology.o stop_task.o pelt.o
objs-$(CONFIG_SCHEDSTATS) += stats.o
objs-$(CONFIG_SCHED_DEBUG) += debug.o

obj-m += scheduler.o
scheduler-objs := $(objs-y) $(sidecar_objs) main.o tainted.o mempool.o sched_rebuild.o

search_cb  := {if (/CALLBACK/) {print "__cb_"$$2} else {print $$2}}
search_und := grep "UND __mod_" | awk '{print substr($$8,7)}' | sort | uniq
search_rdf := grep "UND __mod_" | awk '{print $$8,substr($$8,7)}'
error_msg  := access non-existent symbol by using __mod_ prefix

obj-stub := $(addprefix $(obj)/, $(scheduler-objs:.o=.stub.o))
und-file := $(obj)/.und
rdf-file  = $(dir $@).$(notdir $@).rdf

cmd_find_sym = 			                                         \
	awk -F'[(,]' '$(search_cb)' $< > $@;                             \
	readelf -sW $(obj-stub) | $(search_und) | tee $(und-file) >> $@; \
	count1=$$(cat $(und-file) | wc -l);                              \
	count2=$$(readelf -sW $(obj-stub) | grep -w -f $(und-file) |     \
		  grep -v '\.' | wc -l);                                 \
	if [ "$$count1" != "$$count2" ]; then                            \
		echo -e '\033[31m'$(error_msg)'\033[0m';                 \
		exit 1;                                                  \
	fi

CFLAGS_core.stub.o := -DMODULE -DSTACKSIZE_MOD=0
CFLAGS_main.stub.o := -DMODULE_FRAME_POINTER=VMLINUX_FRAME_POINTER
$(obj)/%.stub.o: $(src)/%.c FORCE
	$(call cmd,force_checksrc)
	$(call if_changed_rule,cc_o_c)

GET_STACK_SIZE: $(obj)/core.stub.o
	$(eval ccflags-y += $(shell bash $(plugsched_tmpdir)/springboard_search.sh build $<))

$(obj)/.globalize: $(src)/export_jump.h $(obj-stub) FORCE
	$(cmd_find_sym)

$(obj)/%.o: $(src)/%.c $(obj)/.globalize GET_STACK_SIZE FORCE
	$(call cmd,force_checksrc)
	$(call if_changed_rule,cc_o_c)
	readelf -sW $@ | $(search_rdf) > $(rdf-file)
	$(OBJCOPY) --globalize-symbols $(obj)/.globalize \
		   --redefine-syms $(rdf-file) $@


ldflags-y += -T $(plugsched_modpath)/scheduler.lds
ccflags-n += -DSCHEDMOD_MEMPOOL
ccflags-y += -Wno-unused-function
ccflags-y += -fkeep-static-functions
