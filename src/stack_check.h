// Copyright 2019-2022 Alibaba Group Holding Limited.
// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

#include <linux/list.h>
#include <trace/events/sched.h>
#include <linux/stacktrace.h>

#define MAX_STACK_ENTRIES	100

extern const char *get_ksymbol(struct module *, unsigned long,
		unsigned long *, unsigned long *);

extern int process_id[];

static void stack_check_init(void)
{
	#define EXPORT_CALLBACK EXPORT_PLUGSCHED
	#define EXPORT_PLUGSCHED(fn, ...) 				\
		kallsyms_lookup_size_offset((unsigned long)__orig_##fn,			 \
				&orig_##fn##_size, NULL); 		\
		vm_func_size[NR_##fn] = orig_##fn##_size;

	#include "export_jump.h"
	#undef EXPORT_PLUGSCHED
	#undef EXPORT_CALLBACK

	vm_func_size[NR___schedule] = 0;
	addr_sort(vm_func_addr, vm_func_size, NR_INTERFACE_FN);

	#define EXPORT_CALLBACK(fn, ...) 				\
		kallsyms_lookup_size_offset((unsigned long)__cb_##fn, 	\
				&mod_##fn##_size, NULL); 		\
		mod_func_size[NR_##fn] = mod_##fn##_size;

	#define EXPORT_PLUGSCHED(fn, ...) 				\
		kallsyms_lookup_size_offset((unsigned long)fn, 		\
				&mod_##fn##_size, NULL); 		\
		mod_func_size[NR_##fn] = mod_##fn##_size;

	#include "export_jump.h"
	#undef EXPORT_PLUGSCHED
	#undef EXPORT_CALLBACK

	mod_func_size[NR___schedule] = 0;
	addr_sort(mod_func_addr, mod_func_size, NR_INTERFACE_FN);
}

static int stack_check_fn(unsigned long *entries, unsigned int nr_entries, bool install)
{
	int i;
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
			continue;
		if (address < func_addr[idx] + func_size[idx])
			return -EAGAIN;
	}

	return 0;
}

#ifdef CONFIG_ARCH_STACKWALK
struct stacktrace_cookie {
	unsigned long	*store;
	unsigned int	size;
	unsigned int	skip;
	unsigned int	len;
};

static bool stack_trace_consume_entry(void *cookie, unsigned long addr)
{
	struct stacktrace_cookie *c = cookie;

	if (c->len >= c->size)
		return false;

	if (c->skip > 0) {
		c->skip--;
		return true;
	}
	c->store[c->len++] = addr;
	return c->len < c->size;
}

static unsigned int get_stack_trace(struct task_struct *tsk,
		unsigned long *store, unsigned int size)
{
	struct stacktrace_cookie c = {
		.store  = store,
		.size   = size,
		.skip   = 0
	};

	if (!try_get_task_stack(tsk))
		return 0;

	arch_stack_walk(stack_trace_consume_entry, &c, tsk, NULL);
	put_task_stack(tsk);
	return c.len;
}
#else
#ifdef CONFIG_X86_64
extern void __save_stack_trace(struct stack_trace *, struct task_struct *,
		struct pt_regs *, bool);

static inline void
save_stack(struct stack_trace *trace, struct task_struct *tsk)
{
	__save_stack_trace(trace, tsk, NULL, false);
}
#else
extern int __save_stack_trace(struct task_struct *, struct stack_trace *,
		unsigned int);

static inline void
save_stack(struct stack_trace *trace, struct task_struct *tsk)
{
	__save_stack_trace(tsk, trace, 0);
}
#endif

static unsigned int get_stack_trace(struct task_struct *tsk,
                       unsigned long *store, unsigned int size)
{
        struct stack_trace trace;

        trace.skip = 0;
        trace.nr_entries = 0;
        trace.max_entries = MAX_STACK_ENTRIES;
        trace.entries = store;

	if (!try_get_task_stack(tsk))
		return 0;

	save_stack(&trace, tsk);
	put_task_stack(tsk);
	return trace.nr_entries;
}
#endif

static int stack_check_task(struct task_struct *task, bool install)
{
	unsigned long entries[MAX_STACK_ENTRIES];
	unsigned int nr_entries;

	nr_entries = get_stack_trace(task, entries, MAX_STACK_ENTRIES);
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
