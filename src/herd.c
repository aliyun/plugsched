#include <linux/types.h>
#include <linux/percpu-defs.h>
#include <linux/slab.h>
#include <linux/sched.h>
#include "sched.h"

struct off_rq_task {
	struct list_head list;
	struct task_struct *task;
	unsigned long ncsw;
};

DEFINE_PER_CPU(struct list_head, off_rq_task_list);

void init_herd(void)
{
	int cpu;

	for_each_online_cpu(cpu) {
		INIT_LIST_HEAD(&per_cpu(off_rq_task_list, cpu));
	}
}

static void save_sleeper(struct task_struct *p, int cpu)
{
	struct off_rq_task *oft;

	printk("Save task %s(%d) to cpu %d\n", p->comm, p->pid, cpu);
	oft = kzalloc(sizeof(*oft), GFP_KERNEL);
	BUG_ON(!oft);
	INIT_LIST_HEAD(&oft->list);
	oft->task = p;
	oft->ncsw = p->nvcsw + p->nivcsw;
	list_add_tail(&oft->list, &per_cpu(off_rq_task_list, cpu));
}

struct cpu_stopper {
	struct task_struct	*thread;

	raw_spinlock_t		lock;
	bool			enabled;	/* is this stopper enabled? */
	struct list_head	works;		/* list of pending works */

	struct cpu_stop_work	stop_work;	/* for stop_cpus */
};

DECLARE_PER_CPU(struct cpu_stopper, cpu_stopper);

void save_sleepers(void)
{
	struct task_struct *g, *p;
	int cpu, first_cpu;

	first_cpu = cpumask_first(cpu_online_mask);
	cpu = smp_processor_id();

	for_each_process_thread(g, p) {
		if (cpu_is_offline(p->cpu) && cpu == first_cpu) {}
		else if (p->cpu == cpu) {}
		else continue;
		save_sleeper(p, cpu);
	}

	save_sleeper(idle_task(cpu), cpu);
	save_sleeper(per_cpu(cpu_stopper, cpu).thread, cpu);
}

extern void __mod_ttwu_do_activate(struct rq *rq, struct task_struct *p,
				int wake_flags, struct rq_flags *rf);
extern void __mod_set_task_cpu(struct task_struct *p, unsigned int new_cpu);

void enqueue_sleepers(bool install)
{
	struct off_rq_task *node, *tmp;
	struct task_struct *p;
	int cpu = smp_processor_id();
	int wake_flags = 0;
	int state;
	struct rq_flags rf;

	if (install)
		return;

	list_for_each_entry_safe(node, tmp, &per_cpu(off_rq_task_list, cpu), list) {
		p = node->task;

		if (p->state & TASK_INTERRUPTIBLE)
			printk("Enqueue S task %s(%d) on cpu %d\n", p->comm, p->pid, p->cpu);
		else if (p->state & TASK_UNINTERRUPTIBLE)
			printk("Enqueue D task %s(%d) on cpu %d\n", p->comm, p->pid, p->cpu);
		else if (p->state == TASK_RUNNING) {
			printk("Ignored R task %s(%d) state=%d on cpu %d\n", p->comm, p->pid, p->state, p->cpu);
			continue;
		} else {
			printk("Enqueue ?? task %s(%d) state=%d on cpu %d\n", p->comm, p->pid, p->state, p->cpu);
		}

		if (p->cpu != cpu) {
			printk("Task %s(%d) has no cpu to run now, migrate to %d\n", p->comm, p->pid, cpu);
			wake_flags |= WF_MIGRATED;
			__mod_set_task_cpu(p, cpu);
		}

		p->sched_contributes_to_load = !!task_contributes_to_load(p);
		state = p->state;
		__mod_ttwu_do_activate(cpu_rq(cpu), p, wake_flags, &rf);
		p->state = state;
	}
}

static bool cpu_wait_for_sleepers(int cpu, bool install)
{
	struct list_head *sleepers = &per_cpu(off_rq_task_list, cpu);
	struct off_rq_task *node, *tmp;
	bool finished = true;
	struct task_struct *p;

	printk("Start checking cpu-%d, list->%p\n", cpu, &per_cpu(off_rq_task_list, cpu));
	list_for_each_entry_safe(node, tmp, sleepers, list) {
		p = node->task;
		if (!install && p->nvcsw + p->nivcsw == node->ncsw) {
			printk("Task %s(%d) hasn't done one round of context_switch\n", p->comm, p->pid);
			finished = false;
		} else {
			printk("Task %s(%d) passed one round of context_switch\n", p->comm, p->pid);
			list_del_init(&node->list);
			kfree(node);
		}
	}

	return finished;
}

void wait_for_sleepers(bool install)
{
	bool finished;
	int cpu;

	while (!finished) {
		finished = true;

		for_each_online_cpu(cpu)
			if (!cpu_wait_for_sleepers(cpu, install))
				finished = false;

		if (!finished) {
			printk("Insmod has to wait for another round of context_switches.");
			set_current_state(TASK_INTERRUPTIBLE);
			schedule_timeout(HZ);
			set_current_state(TASK_RUNNING);
		}
	}
}