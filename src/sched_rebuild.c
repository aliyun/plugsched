/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#include <linux/sched.h>
#include "sched.h"

extern void __orig_set_rq_offline(struct rq*);
extern void __orig_set_rq_online(struct rq*);
extern unsigned int process_id[];

extern const struct sched_class __orig_stop_sched_class;
extern const struct sched_class __orig_dl_sched_class;
extern const struct sched_class __orig_rt_sched_class;
extern const struct sched_class __orig_fair_sched_class;
extern const struct sched_class __orig_idle_sched_class;

static struct sched_class* class[][7] = {
	{
		&fair_sched_class,
		&rt_sched_class,
		&rt_sched_class,
		&fair_sched_class,
		NULL,
		&idle_sched_class,
		&dl_sched_class,
		&stop_sched_class,
	},
	{
		&__orig_fair_sched_class,
		&__orig_rt_sched_class,
		&__orig_rt_sched_class,
		&__orig_fair_sched_class,
		NULL,
		&__orig_idle_sched_class,
		&__orig_dl_sched_class,
		&__orig_stop_sched_class,
	}
};

static void switch_sched_class(struct task_struct *p, int mod)
{
	int policy = p->policy;
	int idx = mod ? 0 : 1;

	if (p == (task_rq(p)->stop))
		policy = 7;

	p->sched_class = class[idx][policy];
	return;
}

void clear_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct rq *rq = this_rq();
	int queue_flags = DEQUEUE_SAVE | DEQUEUE_MOVE | DEQUEUE_NOCLOCK;

	raw_spin_lock(&rq->lock);
	if (mod) {
		set_rq_offline(rq);
	} else {
		__orig_set_rq_offline(rq);
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
	raw_spin_unlock(&rq->lock);
}

void rebuild_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct task_group *tg;
	struct rq *rq = this_rq();
	int queue_flags = ENQUEUE_RESTORE | ENQUEUE_MOVE | ENQUEUE_NOCLOCK;
	int cpu = smp_processor_id();

	raw_spin_lock(&rq->lock);
	if (mod) {
		set_rq_online(rq);
	} else {
		__orig_set_rq_online(rq);
	}

	for_each_process_thread(g, p) {
		if (rq != task_rq(p))
			continue;

		switch_sched_class(p, mod);

		if (p == rq->stop)
			continue;

		if (task_on_rq_queued(p))
			p->sched_class->enqueue_task(rq, p, queue_flags);
	}
	raw_spin_unlock(&rq->lock);

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
