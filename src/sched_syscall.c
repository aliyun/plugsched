#include <asm/unistd.h>
#include <asm/syscall.h>

#ifdef CONFIG_X86_64
# define SYSCALL(name) __x64_sys_##name
#elif defined(CONFIG_X86_32)
# define SYSCALL(name) __ia32_sys_##name
#elif defined(CONFIG_ARM64)
# define SYSCALL(name) __arm64_sys_##name
#else
# define SYSCALL(name) name
#endif

#define NR(name) __NR_##name

#define EXTERN_SYSCALL_DECLARE(name)	\
	extern long SYSCALL(name)(const struct pt_regs *regs)

#define NR_POINTER(name)		\
	{				\
		.nr = NR(name),		\
		.ptr = SYSCALL(name)	\
	}

struct nr_pointer {
	unsigned long nr;
	void *ptr;
};

static int sched_syscall_num;

EXTERN_SYSCALL_DECLARE(sched_get_priority_max);
EXTERN_SYSCALL_DECLARE(sched_get_priority_min);
EXTERN_SYSCALL_DECLARE(sched_getaffinity);
EXTERN_SYSCALL_DECLARE(sched_getattr);
EXTERN_SYSCALL_DECLARE(sched_getparam);
EXTERN_SYSCALL_DECLARE(sched_getscheduler);
EXTERN_SYSCALL_DECLARE(sched_rr_get_interval);
EXTERN_SYSCALL_DECLARE(sched_setaffinity);
EXTERN_SYSCALL_DECLARE(sched_setattr);
EXTERN_SYSCALL_DECLARE(sched_setparam);
EXTERN_SYSCALL_DECLARE(sched_setscheduler);
EXTERN_SYSCALL_DECLARE(sched_yield);
#if defined(CONFIG_X86_32) || defined(CONFIG_ARM64)
EXTERN_SYSCALL_DECLARE(nice);
#endif

static struct nr_pointer sched_syscall_list[] =
{
	NR_POINTER(sched_get_priority_max),
	NR_POINTER(sched_get_priority_min),
	NR_POINTER(sched_getaffinity),
	NR_POINTER(sched_getattr),
	NR_POINTER(sched_getparam),
	NR_POINTER(sched_getscheduler),
	NR_POINTER(sched_rr_get_interval),
	NR_POINTER(sched_setaffinity),
	NR_POINTER(sched_setattr),
	NR_POINTER(sched_setparam),
	NR_POINTER(sched_setscheduler),
	NR_POINTER(sched_yield),
#if defined(CONFIG_X86_32) || defined(CONFIG_ARM64)
	NR_POINTER(nice),
#endif
};

void install_sched_syscall(void)
{
	int i, nr;
	void *old_ptr;

	sched_syscall_num = sizeof(sched_syscall_list) / sizeof(struct nr_pointer);
	for (i = 0; i < sched_syscall_num; i++) {
		nr = sched_syscall_list[i].nr;
		old_ptr = sys_call_table[nr];
		memcpy(sys_call_table+nr, &sched_syscall_list[i].ptr, sizeof(void*));
		sched_syscall_list[i].ptr = old_ptr;
	}
}

void restore_sched_syscall(void)
{
	int i;

	for (i = 0; i < sched_syscall_num; i++)
		memcpy(sys_call_table+sched_syscall_list[i].nr, &sched_syscall_list[i].ptr, sizeof(void*));
}

