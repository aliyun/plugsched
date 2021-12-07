#include <linux/sched.h>
#include "../sched.h"

static void (*orig_unthrottle_cfs_rq)(struct cfs_rq *);

extern struct static_key __cfs_bandwidth_used;

extern atomic_t check_result;
extern unsigned int process_id[];

void init_sched_rebuild(void)
{
	orig_unthrottle_cfs_rq =
		(void (*) (struct cfs_rq *))
			kallsyms_lookup_name("unthrottle_cfs_rq");
}

static inline void clear_bandwidth(struct task_group *tg, bool mod)
{
	struct cfs_rq *cfs_rq;
	struct cfs_bandwidth *cfs_b = &tg->cfs_bandwidth;
	bool cfs_bandwidth_used = static_key_false(&__cfs_bandwidth_used);

	if (!cfs_bandwidth_used || tg == &root_task_group)
		return;

	/* Force unthrottle the throttled cfs rq. */
	list_for_each_entry_rcu(cfs_rq, &cfs_b->throttled_cfs_rq, throttled_list) {
		if (cfs_rq->throttled) {
			if (mod)
				unthrottle_cfs_rq(cfs_rq);
			else
				orig_unthrottle_cfs_rq(cfs_rq);
		}
	}

	/* Deactivate the timer of cfs bandwidth, see destroy_cfs_bandwidth*/
	if (cfs_b->throttled_cfs_rq.next && cfs_b->period_active) {
		hrtimer_cancel(&cfs_b->period_timer);
		hrtimer_cancel(&cfs_b->slack_timer);
	}
}

void clear_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct task_group *tg;
	struct rq *rq;
	int queue_flags = DEQUEUE_SAVE | DEQUEUE_MOVE | DEQUEUE_NOCLOCK;

	list_for_each_entry_rcu(tg, &task_groups, list)
		clear_bandwidth(tg, mod);

	for_each_process_thread(g, p) {
		rq = task_rq(p);

		if (p == rq->stop)
			continue;

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
	int __check_result, cpu = smp_processor_id();

	if (!process_id[cpu])
		atomic_set(&check_result, 0);

	while (__check_result = atomic_read(&check_result)) {
		if (__check_result == -1)
			return;
		cpu_relax();
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

	/* Restart the cfs bandwidth timer */
	list_for_each_entry_rcu(tg, &task_groups, list) {
		if (tg == &root_task_group || !tg->cfs_bandwidth.period_active)
			continue;

		hrtimer_restart(&tg->cfs_bandwidth.period_timer);
		hrtimer_restart(&tg->cfs_bandwidth.slack_timer);
	}
}
