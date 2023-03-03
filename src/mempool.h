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

#define FIELD_TYPE(t, f) typeof(((struct t*)0)->f)
#define FIELD_INDIRECT_TYPE(t, f) typeof(*((struct t*)0)->f)

#define DEFINE_RESERVE(type, field, name, require, max)			\
static struct simple_mempool *name##_smp = NULL;			\
void release_##name##_reserve(struct type *x)			\
{									\
	if (!is_simple_mempool_addr(name##_smp, x->field))		\
		kfree(x->field);					\
	x->field = NULL;						\
}									\
FIELD_TYPE(type, field) alloc_##name##_reserve(void)		\
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
void release_##name##_reserve(struct type *x)			\
{									\
	if (!is_simple_percpu_mempool_addr(name##_smp, x->field))	\
		free_percpu((void *)x->field);				\
	x->field = NULL;						\
}									\
FIELD_TYPE(type, field) alloc_##name##_reserve(void)		\
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
extern int recheck_smps(void);
extern void sched_alloc_extrapad(void);
extern void sched_free_extrapad(void);
extern int sched_mempools_create(void);
extern int sched_mempools_destroy(void);
#else
static inline int recheck_smps(void) { return 0; }
static inline void sched_alloc_extrapad(void) { }
static inline void sched_free_extrapad(void) { }
static inline int sched_mempools_create(void) { return 0; }
static inline int sched_mempools_destroy(void) { return 0; }
#endif /* SCHEDMOD_MEMPOOL */
