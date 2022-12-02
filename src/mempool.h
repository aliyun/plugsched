/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#ifdef SCHEDMOD_MEMPOOL

#include <linux/percpu.h>

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

struct simple_percpu_mempool {
	/* The base address of each percpu memory area. */
	unsigned long		*percpu_ptr;
	/* Record the areas' allocated size. */
	unsigned long		allocated_size;
	unsigned int		obj_size;
	/* How many areas are required for the mempool. */
	unsigned int		areas;
	/* How many objs can be assigned from each area. */
	unsigned int		objs_per_area;
	/* Used to record which area is allocated from. */
	unsigned int		area_id;
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

static struct simple_percpu_mempool *simple_percpu_mempool_create(int obj_num,
		int obj_size)
{
	unsigned int areas, objs_per_area, cnt = 0;
	struct simple_percpu_mempool *psmpool;
	void *ptr;

	psmpool = kzalloc_node(sizeof(*psmpool), GFP_ATOMIC, 0);
	if (!psmpool)
		return NULL;

	/* Calculate how many percpu areas are required. */
	objs_per_area = PCPU_MIN_UNIT_SIZE / obj_size;
	areas = (obj_num + objs_per_area - 1) / objs_per_area;

	psmpool->percpu_ptr =
		kzalloc_node(sizeof(unsigned long) * areas, GFP_ATOMIC, 0);
	if (!psmpool->percpu_ptr)
		goto error;

	for (cnt = 0; cnt < areas; cnt++) {
		ptr = __alloc_percpu(PCPU_MIN_UNIT_SIZE, obj_size);
		if (!ptr)
			goto error;

		psmpool->percpu_ptr[cnt] = (unsigned long)ptr;
	}

	psmpool->obj_size = obj_size;
	psmpool->objs_per_area = objs_per_area;
	psmpool->areas = areas;

	return psmpool;

error:
	while (cnt > 0)
		free_percpu((void *)psmpool->percpu_ptr[--cnt]);

	kfree(psmpool->percpu_ptr);
	kfree(psmpool);

	return NULL;
}

static void *simple_percpu_mempool_alloc(struct simple_percpu_mempool *psmpool)
{
	unsigned long area_size, ret;

	area_size = psmpool->obj_size * psmpool->objs_per_area;

	if ((psmpool->allocated_size + psmpool->obj_size) > area_size) {
		psmpool->area_id++;
		psmpool->allocated_size = 0;
	}

	ret = psmpool->percpu_ptr[psmpool->area_id] + psmpool->allocated_size;
	psmpool->allocated_size += psmpool->obj_size;

	return (void *)ret;
}

static void simple_percpu_mempool_destory(struct simple_percpu_mempool *psmpool)
{
	int i;

	for (i = 0; i < psmpool->areas; i++)
		free_percpu((void *)psmpool->percpu_ptr[i]);

	kfree(psmpool->percpu_ptr);
	kfree(psmpool);
}

static inline bool is_simple_percpu_mempool_addr(
		struct simple_percpu_mempool *psmpool, void *_addr)
{
	int i;
	unsigned long addr, area_size, base;

	addr = (unsigned long)_addr;
	area_size = psmpool->obj_size * psmpool->objs_per_area;

	for (i = 0; i < psmpool->areas; i++) {
		base = psmpool->percpu_ptr[i];
		if (addr >= base && addr < (base + area_size))
			return true;
	}

	return false;
}

#define FIELD_TYPE(t, f) typeof(((struct t*)0)->f)
#define FIELD_INDIRECT_TYPE(t, f) typeof(*((struct t*)0)->f)

#define DEFINE_RESERVE(type, field, name, require, max)			\
static struct simple_mempool *name##_smp = NULL;			\
static void release_##name##_reserve(struct type *x)			\
{									\
	if (!is_simple_mempool_addr(name##_smp, x->field))		\
		kfree(x->field);					\
	x->field = NULL;						\
}									\
static FIELD_TYPE(type, field) alloc_##name##_reserve(void)		\
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

#define DEFINE_RESERVE_PERCPU(type, field, name, require, max)		\
static struct simple_percpu_mempool *name##_smp = NULL;			\
static void release_##name##_reserve(struct type *x)			\
{									\
	if (!is_simple_percpu_mempool_addr(name##_smp, x->field))	\
		free_percpu((void *)x->field);				\
	x->field = NULL;						\
}									\
static FIELD_TYPE(type, field) alloc_##name##_reserve(void)		\
{									\
	return simple_percpu_mempool_alloc(name##_smp);			\
}									\
static int create_mempool_##name(void)					\
{									\
	name##_smp = simple_percpu_mempool_create(max,			\
			sizeof(FIELD_INDIRECT_TYPE(type, field)));	\
	if (!name##_smp)						\
		return -ENOMEM;						\
	return 0;							\
}									\
static int recheck_mempool_##name(void) 				\
{									\
	if (require > (name##_smp->areas * name##_smp->objs_per_area))	\
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
 *
 * DEFINE_RESERVE_PERCPU(task_struct,	// struct task_struct
 * 		percpu_var,		// task_struct's new percpu_var feild
 * 		percpu_var,		// name the percpu mempool as percpu_var_smp
 * 		nr_threads + nr_cpu_ids,//  we need exactly nr_cpu_ids objects
 * 		nr_threads + nr_cpu_ids)// we alloc nr_cpu_ids objects before stop_machine
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

	 * if (err = create_mempool_percpu_var())
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
	 * simple_percpu_mempool_destory(percpu_var_smp);
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

	 * if ((err = recheck_mempool_percpu_var()))
	 *	return err;
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

	 * for_each_process_thread (p, t)
	 *	t->percpu_var = alloc_percpu_var_reserve();
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

	 * for_each_process_thread(p, t)
	 * 	release_percpu_var_reserve(t);
	 */
}

#else
static inline int recheck_smps(void) { return 0; }
static inline void sched_alloc_extrapad(void) { }
static inline void sched_free_extrapad(void) { }
static inline int sched_mempools_create(void) { return 0; }
static inline int sched_mempools_destroy(void) { return 0; }
#endif /* SCHEDMOD_MEMPOOL */
