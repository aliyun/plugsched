// Copyright 2019-2022 Alibaba Group Holding Limited.
// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

#include <linux/list.h>
#include <trace/events/sched.h>
#include <linux/stacktrace.h>
#include "helper.h"

#define MAX_STACK_ENTRIES	100

extern const char *get_ksymbol(struct module *, unsigned long,
		unsigned long *, unsigned long *);

extern int process_id[];

static void stack_check_init(void)
{
	#define PLUGSCHED_FN_PTR EXPORT_PLUGSCHED
	#define EXPORT_PLUGSCHED(fn, ...) 				\
		kallsyms_lookup_size_offset(orig_##fn, 			\
				&orig_##fn##_size, NULL); 		\
		vm_func_size[NR_##fn] = orig_##fn##_size;

	#include "export_jump.h"
	#undef EXPORT_PLUGSCHED
	#undef PLUGSCHED_FN_PTR

	addr_sort(vm_func_addr, vm_func_size, NR_INTERFACE_FN);

	#define PLUGSCHED_FN_PTR(fn, ...) 				\
		kallsyms_lookup_size_offset((unsigned long)__mod_##fn, 	\
				&mod_##fn##_size, NULL); 		\
		mod_func_size[NR_##fn] = mod_##fn##_size;

	#define EXPORT_PLUGSCHED(fn, ...) 				\
		kallsyms_lookup_size_offset((unsigned long)fn, 		\
				&mod_##fn##_size, NULL); 		\
		mod_func_size[NR_##fn] = mod_##fn##_size;

	#include "export_jump.h"
	#undef EXPORT_PLUGSCHED
	#undef PLUGSCHED_FN_PTR

	addr_sort(mod_func_addr, mod_func_size, NR_INTERFACE_FN);
}

static int stack_check_fn(unsigned long *entries, unsigned int nr_entries, bool install)
{
	int i, ret;
	unsigned long *func_addr;
	unsigned long *func_size;

	if (install) {
		func_addr = vm_func_addr;
		func_size = vm_func_size;
	} else {
		func_addr = mod_func_addr;
		func_size = mod_func_size;
	}

	for (i = 0; i < nr_entries; i++) {
		int idx;
		unsigned long address = entries[i];

		idx = bsearch(func_addr, 0, NR_INTERFACE_FN - 1, address);
		if (idx == -1)
			return 0;
		if (address < func_addr[idx] + func_size[idx])
			return -EAGAIN;
	}

	return 0;
}

/* This is basically copied from klp_check_stack */
static int stack_check_task(struct task_struct *task, bool install)
{
	unsigned long entries[MAX_STACK_ENTRIES];
	unsigned int nr_entries = 0;

#ifndef CONFIG_ARCH_STACKWALK
	struct stack_trace trace;

	trace.skip = 0;
	trace.nr_entries = 0;
	trace.max_entries = MAX_STACK_ENTRIES;
	trace.entries = entries;

	save_stack_trace_tsk(task, &trace);
	nr_entries = trace.nr_entries;
#else /* CONFIG_ARCH_STACKWALK */
	nr_entries = stack_trace_save_tsk(task, &entries, MAX_STACK_ENTRIES, 0);
#endif /* CONFIG_ARCH_STACKWALK */

	return stack_check_fn(entries, nr_entries, install);
}

static int stack_check(bool install)
{
	struct task_struct *p, *t;
	int task_count = 0;
	int nr_cpus = num_online_cpus();
	int cpu = smp_processor_id();

	for_each_process_thread(p, t) {
		if ((task_count % nr_cpus) == process_id[cpu]) {
			if (stack_check_task(t, install))
				return -EBUSY;
		}
		task_count++;
	}

	t = idle_task(cpu);
	if (stack_check_task(t, install))
		return -EBUSY;

	return 0;
}
