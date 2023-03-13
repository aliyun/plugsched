/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#include <linux/sched.h>
#include <linux/version.h>
#include "sched.h"
#include "helper.h"

extern void __orig_set_rq_offline(struct rq*);
extern void __orig_set_rq_online(struct rq*);
extern void __orig_update_rq_clock(struct rq *rq);

extern void __mod_set_rq_offline(struct rq*);
extern void __mod_set_rq_online(struct rq*);
extern void __mod_update_rq_clock(struct rq *rq);

extern unsigned int process_id[];

extern struct sched_class __orig_stop_sched_class;
extern struct sched_class __orig_dl_sched_class;
extern struct sched_class __orig_rt_sched_class;
extern struct sched_class __orig_fair_sched_class;
extern struct sched_class __orig_idle_sched_class;
extern struct sched_class shadow_stop_sched_class;
extern struct sched_class shadow_dl_sched_class;
extern struct sched_class shadow_rt_sched_class;
extern struct sched_class shadow_fair_sched_class;
extern struct sched_class shadow_idle_sched_class;

struct sched_class *orig_class[] = {
	&__orig_stop_sched_class,
	&__orig_dl_sched_class,
	&__orig_rt_sched_class,
	&__orig_fair_sched_class,
	&__orig_idle_sched_class,
};

struct sched_class *mod_class[] = {
	&shadow_stop_sched_class,
	&shadow_dl_sched_class,
	&shadow_rt_sched_class,
	&shadow_fair_sched_class,
	&shadow_idle_sched_class,
};

DEFINE_PER_CPU(struct list_head, dying_task_list);

#define NR_SCHED_CLASS 5
struct sched_class bak_class[NR_SCHED_CLASS];

#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 3, 0)

extern struct task_struct __orig_fake_task;

#define pick_next_task_rq(class, rf) \
	(class)->pick_next_task(rq, &__orig_fake_task, &(rf))

#else
#define pick_next_task_rq(class, rf) \
	(class)->pick_next_task(rq)
#endif

void switch_sched_class(bool mod)
{
	int i;
	int size = sizeof(struct sched_class);

	for (i = 0; i < NR_SCHED_CLASS; i++) {
		void *waddr;

		waddr = disable_write_protect(orig_class[i]);

		if (mod) {
			memcpy(&bak_class[i], waddr, size);
			memcpy(waddr, mod_class[i], size);
		} else {
			memcpy(waddr, &bak_class[i], size);
		}

		enable_write_protect();
	}
}

void clear_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct rq *rq = this_rq();
	struct rq_flags rf;
	int queue_flags = DEQUEUE_SAVE | DEQUEUE_MOVE | DEQUEUE_NOCLOCK;
	int cpu = smp_processor_id();

	rq_lock(rq, &rf);

	if (mod) {
		__mod_update_rq_clock(rq);
		__mod_set_rq_offline(rq);
	} else {
		__orig_update_rq_clock(rq);
		__orig_set_rq_offline(rq);
	}

	for_each_process_thread(g, p) {
		if (rq != task_rq(p))
			continue;

		if (p == rq->stop)
			continue;

		if (task_on_rq_queued(p))
			p->sched_class->dequeue_task(rq, p, queue_flags);
	}

	INIT_LIST_HEAD(&per_cpu(dying_task_list, cpu));

	/* This logic comes from sched_cpu_dying to deal with dying tasks. */
	for (;;) {
		const struct sched_class *class;
		struct task_struct *next;

		/* Now, just the stopper task is running. */
		if (rq->nr_running == 1)
			break;

		for_each_class(class) {
			next = pick_next_task_rq(class, rf);
			if (next) {
				next->sched_class->put_prev_task(rq, next);
				next->sched_class->dequeue_task(rq, p, queue_flags);
				list_add_tail_rcu(&p->tasks, &per_cpu(dying_task_list, cpu));
				break;
			}
		}
	}
	rq_unlock(rq, &rf);
}

void rebuild_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct task_group *tg;
	struct rq *rq = this_rq();
	struct rq_flags rf;
	int queue_flags = ENQUEUE_RESTORE | ENQUEUE_MOVE | ENQUEUE_NOCLOCK;
	int cpu = smp_processor_id();

	rq_lock(rq, &rf);

	if (mod) {
		__mod_update_rq_clock(rq);
		__mod_set_rq_online(rq);
	} else {
		__orig_update_rq_clock(rq);
		__orig_set_rq_online(rq);
	}

	for_each_process_thread(g, p) {
		if (rq != task_rq(p))
			continue;

		if (p == rq->stop)
			continue;

		if (task_on_rq_queued(p))
			p->sched_class->enqueue_task(rq, p, queue_flags);
	}

	list_for_each_entry_rcu(p, &per_cpu(dying_task_list, cpu), tasks) {
		p->sched_class->enqueue_task(rq, p, queue_flags);
		list_del_init(&p->tasks);
	}
	rq_unlock(rq, &rf);

	if (process_id[cpu])
		return;

	/* Restart the cfs/rt bandwidth timer */
	list_for_each_entry_rcu(tg, &task_groups, list) {
		if (tg == &root_task_group)
			continue;

		if (tg->cfs_bandwidth.period_active) {
			hrtimer_restart(&tg->cfs_bandwidth.period_timer);
			hrtimer_restart(&tg->cfs_bandwidth.slack_timer);
		}
#ifdef CONFIG_RT_GROUP_SCHED
		if (tg->rt_bandwidth.rt_period_active)
			hrtimer_restart(&tg->rt_bandwidth.rt_period_timer);
#endif
	}
}
