/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#include <linux/sched.h>
#include "sched.h"

static void (*orig_set_rq_offline)(struct rq *);
static void (*orig_set_rq_online)(struct rq *);

extern unsigned int process_id[];

void init_sched_rebuild(void)
{
	orig_set_rq_online = (void (*) (struct rq *))
			kallsyms_lookup_name("set_rq_online");
	orig_set_rq_offline = (void (*) (struct rq *))
			kallsyms_lookup_name("set_rq_offline");
}

void clear_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct rq *rq = this_rq();
	int queue_flags = DEQUEUE_SAVE | DEQUEUE_MOVE | DEQUEUE_NOCLOCK;

	if (mod) {
		set_rq_offline(rq);
	} else {
		orig_set_rq_offline(rq);
	}

	for_each_process_thread(g, p) {
		if (rq != task_rq(p))
			continue;

		if (p == rq->stop)
			continue;

		/* To avoid SCHED_WARN_ON(rq->clock_update_flags < RQCF_ACT_SKIP) */
		rq->clock_update_flags = RQCF_UPDATED;

		if (task_on_rq_queued(p))
			p->sched_class->dequeue_task(rq, p, queue_flags);
	}
}

void rebuild_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct task_group *tg;
	struct rq *rq = this_rq();
	int queue_flags = ENQUEUE_RESTORE | ENQUEUE_MOVE | ENQUEUE_NOCLOCK;
	int cpu = smp_processor_id();

	if (mod) {
		set_rq_online(rq);
	} else {
		orig_set_rq_online(rq);
	}

	for_each_process_thread(g, p) {
		if (rq != task_rq(p))
			continue;

		if (p == rq->stop)
			continue;

		if (task_on_rq_queued(p))
			p->sched_class->enqueue_task(rq, p, queue_flags);
	}

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
