#include <linux/sched.h>
#include "../sched.h"

unsigned long vm_init_entity_runnable_average;
unsigned long vm_unthrottle_cfs_rq;
unsigned long vm_post_init_entity_util_avg;

extern void enqueue_task_fair(struct rq *rq, struct task_struct *p, int flags);
extern void __mod_enqueue_task_fair(struct rq *rq, struct task_struct *p, int flags);

extern struct static_key __cfs_bandwidth_used;

extern atomic_t check_result;
extern unsigned int process_id[];

void init_sched_rebuild(void)
{
        vm_init_entity_runnable_average =
                kallsyms_lookup_name("init_entity_runnable_average");
        vm_unthrottle_cfs_rq =
                kallsyms_lookup_name("unthrottle_cfs_rq");
        vm_post_init_entity_util_avg =
                kallsyms_lookup_name("post_init_entity_util_avg");
}

static noinline void clear_rq(struct rq *rq, bool mod)
{
	/*
	 * under new sync framework, nobody will hold any rq->lock.
	 * don't need to reinit rq->lock
	 */
	rq->nr_running = 0;

	/* disable numa balancing to make reset easy */
	rq->nr_numa_running = 0;
	rq->nr_preferred_running = 0;
	rq->numa_migrate_on = 0;

	INIT_LIST_HEAD(&rq->leaf_cfs_rq_list);
	rq->tmp_alone_branch = &rq->leaf_cfs_rq_list;
	INIT_LIST_HEAD(&rq->cfs_tasks);

	rq->clock_update_flags = RQCF_UPDATED;

#ifdef CONFIG_GROUP_IDENTITY
	rq->nr_high_running = 0;
	rq->nr_under_running = 0;
	rq->nr_expel_immune = 0;
	rq->nr_high_make_up = 0;
	rq->nr_under_make_up = 0;
	rq->on_expel = 0;
	rq->avg_id_idle = 0;
#ifdef CONFIG_SCHED_SMT
	rq->next_expel_ib = 0;
	rq->next_expel_update = 0;
#endif
#endif

	/*
	 * task in rq->wake_list will be handled by sched_ttwu_pending:
	 *	1. scheduler_ipi;
	 *	2. return from halt (do_idle);
	 * So, don't clear wake_list.
	 */
}

static noinline void clear_cfs_rq(struct cfs_rq *cfs_rq, bool mod)
{
	memset(&cfs_rq->load, 0, sizeof(cfs_rq->load));
	cfs_rq->runnable_weight = 0;
	cfs_rq->nr_running = 0;
	cfs_rq->h_nr_running = 0;

	cfs_rq->tasks_timeline = RB_ROOT_CACHED;

	/* clear lots of member */
	memset(&cfs_rq->curr, 0, offsetof(struct cfs_rq, rq) - offsetof(struct cfs_rq, curr));
	/* under new sync framework, nobody will hold removed.lock */
	raw_spin_lock_init(&cfs_rq->removed.lock);

	cfs_rq->on_list = 0;
	INIT_LIST_HEAD(&cfs_rq->leaf_cfs_rq_list);

#ifdef CONFIG_GROUP_IDENTITY
	cfs_rq->nr_tasks = 0;
	cfs_rq->min_under_vruntime = (u64)(-(1LL << 20));
#ifdef CONFIG_SCHED_SMT
	cfs_rq->expel_spread = 0;
	cfs_rq->expel_start = 0;
	cfs_rq->h_nr_expel_immune = 0;
	INIT_LIST_HEAD(&cfs_rq->expel_list);
#endif
	cfs_rq->under_timeline = RB_ROOT_CACHED;
#endif
}

/*
 * Do clear topo of se & tg.
 */
static noinline void clear_tg_se(struct sched_entity *se, bool mod)
{
	memset(&se->run_node, 0, sizeof(se->run_node));
	INIT_LIST_HEAD(&se->group_node);
	se->on_rq = 0;

	/* Clear load, update_load_set */
	se->load.weight = NICE_0_LOAD;
	se->load.inv_weight = 0;

	if (mod)
		init_entity_runnable_average(se);
	else
		((void (*) (struct sched_entity *))(vm_init_entity_runnable_average))(se);

	se->vruntime = 0;

#if defined(CONFIG_GROUP_IDENTITY) && defined(CONFIG_SCHED_SMT)
	INIT_LIST_HEAD(&se->expel_node);
#endif

	/* don't clear sched statistics */
}

static noinline void clear_tg(struct task_group *tg, bool mod)
{
	struct cfs_bandwidth *cfs_b = &tg->cfs_bandwidth;
	struct cfs_rq *cfs_rq;
	bool cfs_bandwidth_used = static_key_false(&__cfs_bandwidth_used);

	atomic_long_set(&tg->load_avg, 0);

	if (!cfs_bandwidth_used || tg == &root_task_group)
		return;

	list_for_each_entry_rcu(cfs_rq, &cfs_b->throttled_cfs_rq, throttled_list)
		/*
		 * Force unthrottle the throttled cfs rq.
		 * There should use the cfs_rq_throttled, but it is inline function.
		 */
		if (cfs_rq->throttled)
			if (mod)
				unthrottle_cfs_rq(cfs_rq);
			else
				((void (*) (struct cfs_rq *))(vm_unthrottle_cfs_rq))(cfs_rq);

	/* Deactivate the timer of cfs bandwidth, see destroy_cfs_bandwidth*/
	if (cfs_b->throttled_cfs_rq.next && cfs_b->period_active) {
		hrtimer_cancel(&cfs_b->period_timer);
		hrtimer_cancel(&cfs_b->slack_timer);
	}
}

static noinline void print_sched_state(int after)
{
	struct rq *rq;
	struct cfs_rq *cfs;
	int i;

	if (after)
		pr_err("\n -------- after rebuild -------- \n");

	for_each_possible_cpu(i) {
		rq = cpu_rq(i);
		cfs = &rq->cfs;
		pr_err("cpu%d: rq.nr_running = %u, rq.curr = %px, cfs.nr_running = %u, cfs.h_nr_running = %u \n",
			i, rq->nr_running, rq->curr, cfs->nr_running, cfs->h_nr_running);
	}
}

/*
 * Ref to alloc_fair_sched_group & sched_init
 */
void clear_sched_state(bool mod)
{
	struct task_group *tg;
	int i;

	list_for_each_entry_rcu(tg, &task_groups, list) {
		for_each_possible_cpu(i) {
			/* root tg->se is NULL */
			if (tg != &root_task_group)
				clear_tg_se(tg->se[i], mod);

			clear_cfs_rq(tg->cfs_rq[i], mod);
		}

		clear_tg(tg, mod);
	}

	for_each_possible_cpu(i)
		clear_rq(cpu_rq(i), mod);
}

/*
 * Only be called from stop_machine context, don't need to
 * hold any lock.
 */
void rebuild_sched_state(bool mod)
{
	struct task_struct *g, *p;
	struct task_group *tg;
	struct rq *rq;
	int __check_result, cpu = smp_processor_id();

	if (!process_id[cpu])
		atomic_set(&check_result, 0);

	while (__check_result = atomic_read(&check_result)) {
		if (__check_result == -1)
			return;
		cpu_relax();
	}

	for_each_process_thread(g, p) {
		if (cpu != p->cpu)
			continue;

		/* TODO: how to deal with TASK_DEAD, TASK_WAKING ... ? */
		rq = cpu_rq(p->cpu);

		if (p->sched_class == &idle_sched_class)
			continue;

		/* TODO: tmp handler for other sched class */
		if (p->sched_class != &fair_sched_class && p->state == TASK_RUNNING) {
			add_nr_running(rq, 1);
			continue;
		}

		/*
		 * NOTE: for TASK_ON_RQ_MIGRATING, p->se_on_rq is 0. Please refer
		 * detach_tasks for more details.
		 */
		if (p->sched_class != &fair_sched_class || !p->on_rq)
			continue;

		/* Fake all running tasks as new tasks. */
		p->on_rq = 0;
		p->se.on_rq = 0;
		p->se.vruntime = 0;

		/*
		 * It's fatal to calc runnable_load_avg, setted to 0 by
		 * migrate_task_rq_fair during migration.
		 */
		p->se.avg.last_update_time = 0;

		/* clear se->run_node, se->group_node */
		memset(&p->se.run_node, 0, sizeof(p->se.run_node));
		INIT_LIST_HEAD(&p->se.group_node);

		/*
		 * rebuild: enqueue running task to cfs
		 *	borrow code from wake_up_new_task()
		 */
		if (mod) {
			post_init_entity_util_avg(&p->se);
			__mod_enqueue_task_fair(rq, p, ENQUEUE_NOCLOCK);
		} else {
			((void (*) (struct sched_entity *))(vm_post_init_entity_util_avg))(&p->se);
			enqueue_task_fair(rq, p, ENQUEUE_NOCLOCK);
		}
		p->on_rq = TASK_ON_RQ_QUEUED;
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
