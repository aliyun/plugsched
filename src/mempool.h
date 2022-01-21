/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#ifdef SCHEDMOD_MEMPOOL

#define is_simple_mempool_addr(smpool, addr) \
	((unsigned long)(addr) >= (smpool)->vstart && \
	 (unsigned long)(addr) <= (smpool)->vend)

struct simple_mempool {
	unsigned long		vstart;
	unsigned long		vend;
	unsigned long		alloc_addr;
	unsigned int 		obj_size;
	unsigned int		obj_num;
};

static inline void *simple_mempool_alloc(struct simple_mempool *smpool)
{
	void *ret;

	ret = smpool->alloc_addr;
	smpool->alloc_addr += smpool->obj_size;

	return ret;
}

static inline struct simple_mempool *simple_mempool_create(int obj_num, int obj_size)
{
	struct simple_mempool *smpool;

	smpool = kzalloc_node(sizeof(*smpool), GFP_ATOMIC, 0);
	if (!smpool)
		return NULL;

	smpool->vstart = vmalloc_node(obj_num * obj_size, 0);
	if (!smpool->vstart) {
		kfree(smpool);
		return NULL;
	}

	smpool->alloc_addr = smpool->vstart;
	smpool->vend = smpool->vstart + obj_num * obj_size;
	smpool->obj_size = obj_size;
	smpool->obj_num = obj_num;

	return smpool;
}

static inline void simple_mempool_destory(struct simple_mempool *smpool)
{
	vfree(smpool->vstart);
	kfree(smpool);
}

#define FIELD_TYPE(t, f) typeof(((struct t*)0)->f)
#define FIELD_INDIRECT_TYPE(t, f) typeof(*((struct t*)0)->f)

#define DEFINE_RESERVE(type, field, name, require, max)			\
struct simple_mempool *name##_smp = NULL;				\
void release_##name##_reserve(struct type *x)				\
{									\
	if (!is_simple_mempool_addr(name##_smp, x->field))		\
		kfree(x->field);					\
	x->field = NULL;						\
}									\
FIELD_TYPE(type, field) alloc_##name##_reserve(void)			\
{									\
	return simple_mempool_alloc(name##_smp);			\
}									\
static int create_mempool_##name(void)					\
{									\
	name##_smp = simple_mempool_create(max,				\
			sizeof(FIELD_INDIRECT_TYPE(type, field)));	\
	if (!name##_smp)						\
		return -ENOMEM;						\
	return 0;							\
}									\
static int recheck_mempool_##name(void)					\
{									\
	if (require > name##_smp->obj_num)				\
		return -ENOMEM;						\
	return 0;							\
}

/*
 * Examples of simple mempool usage

 * #define nr_tgs atomic_read(&cpu_cgrp_subsys.root->nr_cgrps)
 *
 * DEFINE_RESERVE(sched_statistics,
 * 		bvt,
 * 		se,
 * 		nr_threads + nr_cpu_ids + (nr_tgs - 1) * nr_cpu_ids,
 * 		(nr_threads + nr_cpu_ids + (nr_tgs - 1) * nr_cpu_ids)*2)
 *
 * DEFINE_RESERVE(rq,		// struct rq
 * 		bvt,		// struct rq's bvt field
 * 		rq,		// name the mempool as rq_smp
 * 		nr_cpu_ids,	// we need exactly nr_cpu_ids objects
 * 		nr_cpu_ids);	// we alloc nr_cpu_ids objects before stop_machine
 */

static int sched_mempools_create(void)
{
	int err;

	/*
	 * Examples of mempools create
	 * if ((err = create_mempool_se()))
	 * 	return err;

	 * if ((err = create_mempool_rq()))
	 * 	return err;
	 */

	return 0;
}

static void sched_mempools_destroy(void)
{
	/*
	 * Examples of mempools destroy
	 * simple_mempool_destory(se_smp);
	 * simple_mempool_destory(rq_smp);
	 */
}

static int recheck_smps(void)
{
	int err = -ENOMEM;

	/*
	 * Examples of mempools recheck
	 * if ((err = recheck_mempool_rq()))
	 * 	return err;

	 * if ((err = recheck_mempool_se()))
	 * 	return err;
	 */

	return 0;
}

static void sched_alloc_extrapad(void)
{
	/* TODO: Exploit all CPUs */

	/*
	 * Examples of alloc extrapad
	 * struct task_struct *p, *t;
	 * struct task_group *tg;
	 * int cpu;

	 * for_each_possible_cpu(cpu) {
	 * 	cpu_rq(cpu)->bvt = alloc_rq_reserve();
	 * 	idle_task(cpu)->se.statistics.bvt = alloc_se_reserve();
	 * }

	 * for_each_process_thread(p, t)
	 * 	t->se.statistics.bvt = alloc_se_reserve();

	 * list_for_each_entry_rcu(tg, &task_groups, list) {
	 * 	if (tg == &root_task_group || task_group_is_autogroup(tg))
	 *		continue;
	 * 	for_each_possible_cpu(cpu)
	 * 		tg->se[cpu]->statistics.bvt = alloc_se_reserve();
	 * }
	 */
}

static void sched_free_extrapad(void)
{
	/* TODO: Exploit all CPUs */

	/*
	 * Examples of free extrapad
	 * struct task_struct *p, *t;
	 * struct task_group *tg;
	 * int cpu;

	 * for_each_possible_cpu(cpu) {
	 * 	release_se_reserve(&idle_task(cpu)->se.statistics);
	 * 	release_rq_reserve(cpu_rq(cpu));
	 * }
	 * for_each_process_thread (p, t)
	 * 	release_se_reserve(&t->se.statistics);

	 * list_for_each_entry_rcu(tg, &task_groups, list) {
	 * 	if (tg == &root_task_group || task_group_is_autogroup(tg))
	 *		continue;
	 * 	for_each_possible_cpu(cpu)
	 * 		release_se_reserve(&tg->se[cpu]->statistics);
	 * }
	 */
}

#else
static inline int recheck_smps(void) { return 0; }
static inline void sched_alloc_extrapad(void) { }
static inline void sched_free_extrapad(void) { }
static inline int sched_mempools_create(void) { return 0; }
static inline int sched_mempools_destroy(void) { return 0; }
#endif /* SCHEDMOD_MEMPOOL */
