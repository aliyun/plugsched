/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#ifndef __HEAD_JUMP_H
#define __HEAD_JUMP_H

#include <linux/cpu.h>
#include <linux/kallsyms.h>

#define EXPORT_SIDECAR(fn, file, ...) EXPORT_PLUGSCHED(fn, __VA_ARGS__)
#define PLUGSCHED_FN_PTR EXPORT_PLUGSCHED
#define EXPORT_PLUGSCHED(fn, ...) NR_##fn,
enum {
	#include "export_jump.h"
	NR_INTERFACE_FN
} nr_inter_fn;
#undef EXPORT_PLUGSCHED
#undef PLUGSCHED_FN_PTR

static unsigned long vm_func_addr[NR_INTERFACE_FN];
static unsigned long vm_func_size[NR_INTERFACE_FN];
static unsigned long mod_func_addr[NR_INTERFACE_FN];
static unsigned long mod_func_size[NR_INTERFACE_FN];

/* Used to declare the extern function set */
#define PLUGSCHED_FN_PTR(fn, ret, ...) extern ret __mod_##fn(__VA_ARGS__);
#define EXPORT_PLUGSCHED(fn, ret, ...) extern ret fn(__VA_ARGS__);
#include "export_jump.h"
#undef EXPORT_PLUGSCHED
#undef PLUGSCHED_FN_PTR

/* Used to declare extern functions defined in vmlinux*/
#define PLUGSCHED_FN_PTR(fn, ret, ...) extern ret __orig_##fn(__VA_ARGS__);
#define EXPORT_PLUGSCHED(fn, ret, ...) extern ret __orig_##fn(__VA_ARGS__);
#include "export_jump.h"
#undef EXPORT_PLUGSCHED
#undef PLUGSCHED_FN_PTR

/* They are completely identical unless specified */
#define PLUGSCHED_FN_PTR EXPORT_PLUGSCHED

/* This APIs set is used to replace the function in vmlinux with other
 * function(have the same name) in module. Usage by fallow:
 *
 * 1) For just one function:
 *    1. DEFINE_JUMP_FUNC(function) 	//define the useful data
 *    2. JUMP_INIT_FUNC(function) 	//init the data
 *    3. JUMP_INSTALL_FUNC(function) 	//replace the funciton
 *    4. JUMP_REMOVE_FUNC(function) 	//restore the function
 *
 * 2) For functions set:
 *    1. Add the function to export_jump.h file
 *    2. Call jump_init_all() to init all functions data
 *    3. Use JUMP_OPERATION(install) macro to replace the functions set
 *    4. Use JUMP_OPERATION(remove) macro to restore the functions set
 */

#ifdef CONFIG_X86_64

#define HEAD_LEN 5

#define DEFINE_JUMP_FUNC(func) 	\
	static unsigned char store_jump_##func[HEAD_LEN]; 	\
	static unsigned char store_orig_##func[HEAD_LEN]; 	\
	static unsigned long orig_##func##_size; 		\
	static unsigned long mod_##func##_size

#define JUMP_INIT_FUNC(func, prefix) do {		\
		vm_func_addr[NR_##func] = (unsigned long)__orig_##func; 	\
		mod_func_addr[NR_##func] = (unsigned long)prefix##func; \
		memcpy(store_orig_##func, __orig_##func, HEAD_LEN); \
		store_jump_##func[0] = 0xe9; 	\
		(*(int *)(store_jump_##func + 1)) = 	\
			(long)prefix##func - (long)__orig_##func - HEAD_LEN; \
	} while(0)

#define JUMP_INSTALL_FUNC(func) \
	memcpy((unsigned char *)orig_##func, store_jump_##func, HEAD_LEN)

#define JUMP_REMOVE_FUNC(func) 	\
	memcpy((unsigned char *)orig_##func, store_orig_##func, HEAD_LEN)

static inline void do_write_cr0(unsigned long val)
{
	asm volatile("mov %0,%%cr0": "+r" (val) : : "memory");
}

/* Must be used in stop machine context */
#define JUMP_OPERATION(ops) do { 	\
		unsigned long cr0;      \
					\
		cr0 = read_cr0();       \
		do_write_cr0(cr0 & 0xfffeffff);    \
		jump_##ops();		\
		do_write_cr0(cr0);         \
	} while(0)

#else /* For ARM64 */
#define DEFINE_JUMP_FUNC(func)				\
	static u32 store_orig_##func;			\
	static u32 store_jump_##func;			\
	static unsigned long orig_##func##_size;	\
	static unsigned long mod_##func##_size

#define JUMP_INIT_FUNC(func, prefix) do {	\
		vm_func_addr[NR_##func] = (unsigned long)__orig_##func; 	\
		mod_func_addr[NR_##func] = (unsigned long)prefix##func; \
		memcpy((void *)&store_orig_##func, __orig_##func, AARCH64_INSN_SIZE); \
		store_jump_##func = aarch64_insn_gen_branch_imm((unsigned long)__orig_##func,	\
				  (unsigned long)prefix##func, AARCH64_INSN_BRANCH_NOLINK); \
	} while(0)

#define JUMP_INSTALL_FUNC(func) \
	aarch64_insn_patch_text_nosync(__orig_##func, store_jump_##func)

#define JUMP_REMOVE_FUNC(func)  \
	aarch64_insn_patch_text_nosync(__orig_##func, store_orig_##func)

#define JUMP_OPERATION(ops) do {	\
		jump_##ops();	\
	} while(0)

#endif /* CONFIG_X86_64 */

#define EXPORT_PLUGSCHED(fn, ...) DEFINE_JUMP_FUNC(fn);
#include "export_jump.h"
#undef EXPORT_PLUGSCHED

#define EXPORT_PLUGSCHED(fn, ...) JUMP_INSTALL_FUNC(fn);
static inline void jump_install(void)
{
	#include "export_jump.h"
}
#undef EXPORT_PLUGSCHED

#define EXPORT_PLUGSCHED(fn, ...) JUMP_REMOVE_FUNC(fn);
static inline void jump_remove(void)
{
	#include "export_jump.h"
}
#undef EXPORT_PLUGSCHED


#undef PLUGSCHED_FN_PTR
#define PLUGSCHED_FN_PTR(fn, prefix, ...) JUMP_INIT_FUNC(fn, __mod_);
#define EXPORT_PLUGSCHED(fn, ...) JUMP_INIT_FUNC(fn, );
static int __maybe_unused jump_init_all(void)
{
	#include "export_jump.h"
	return 0;
}
#undef EXPORT_PLUGSCHED
#undef PLUGSCHED_FN_PTR

#endif
