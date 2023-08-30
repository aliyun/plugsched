/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

#include <linux/module.h>
#include <linux/init.h>
#include <linux/printk.h>
#include <linux/path.h>
#include <linux/mutex.h>
#include <linux/namei.h>
#include <linux/sched/task.h>
#include <linux/sysfs.h>
#include <linux/version.h>
#include "sched.h"
#include "helper.h"
#include "mempool.h"
#include "head_jump.h"
#include "stack_check.h"

#define CHECK_STACK_LAYOUT() \
	BUILD_BUG_ON_MSG(MODULE_FRAME_POINTER != VMLINUX_FRAME_POINTER, \
		"stack layout of __schedule can not match to it in vmlinux")

#define MAX_CPU_NR		1024

extern void __orig___schedule(bool);
int process_id[MAX_CPU_NR];
atomic_t cpu_finished;
atomic_t clear_finished;
atomic_t redirect_finished;
static atomic_t global_error;

DECLARE_PER_CPU(struct callback_head, dl_push_head);
DECLARE_PER_CPU(struct callback_head, dl_pull_head);
DECLARE_PER_CPU(struct callback_head, rt_push_head);
DECLARE_PER_CPU(struct callback_head, rt_pull_head);
unsigned long sched_springboard;

extern struct mutex cgroup_mutex;

#if LINUX_VERSION_CODE < KERNEL_VERSION(5, 3, 0)
extern struct mutex cpuset_mutex;
#define plugsched_cpuset_lock() \
	mutex_lock(&cpuset_mutex)
#define plugsched_cpuset_unlock() \
	mutex_unlock(&cpuset_mutex)
#else
extern struct percpu_rw_semaphore cpuset_rwsem;
#define plugsched_cpuset_lock() \
	percpu_down_write(&cpuset_rwsem)
#define plugsched_cpuset_unlock() \
	percpu_up_write(&cpuset_rwsem)
#endif

extern cpumask_var_t sd_sysctl_cpus;
extern const struct file_operations __mod_sched_feat_fops;
extern const struct seq_operations __mod_sched_debug_sops;
extern const struct seq_operations __mod_schedstat_sops;

static struct dentry *sched_features_dir;
static s64 stop_time;
ktime_t stop_time_p0, stop_time_p1, stop_time_p2;
ktime_t main_start, main_end, init_start, init_end;

extern void clear_sched_state(bool mod);
extern void rebuild_sched_state(bool mod);
extern void switch_sched_class(bool mod);

static int scheduler_enable = 0;
struct kobject *plugsched_dir, *plugsched_subdir, *vmlinux_moddir;

struct tainted_function {
	char *name;
	struct kobject *kobj;
};

#undef TAINTED_FUNCTION
#define TAINTED_FUNCTION(func,sympos) 		\
	{ 					\
		.name = #func "," #sympos,	\
		.kobj = NULL,			\
	},

struct tainted_function tainted_functions[] = {
	#include "tainted_functions.h"
	{}
};

static inline void parallel_state_check_init(void)
{
	atomic_set(&cpu_finished, num_online_cpus());
	atomic_set(&clear_finished, num_online_cpus());
	atomic_set(&redirect_finished, num_online_cpus());
	atomic_set(&global_error, 0);
}

static inline void process_id_init(void)
{
	int cpu, idx = 0;

	for_each_online_cpu(cpu)
		process_id[cpu] = idx++;
}

static bool is_first_process(void)
{
	return process_id[smp_processor_id()] == 0;
}

static void print_error(int error)
{
	if (is_first_process()) {
		if (error == -ENOMEM) {
			printk("scheduler: Error: not enough memory for mempool!\n");
		} else if(error == -EBUSY) {
			printk("scheduler: Error: Device or resources busy!\n");
		} else {
			printk("scheduler: Error: Unknown\n");
		}
	}
}

static void reset_balance_callback(void)
{
	int i;

	for_each_possible_cpu(i) {
		struct rq *rq = cpu_rq(i);

		rq->balance_callback = NULL;
		per_cpu_ptr(&dl_push_head, i)->next = NULL;
		per_cpu_ptr(&dl_pull_head, i)->next = NULL;
		per_cpu_ptr(&rt_push_head, i)->next = NULL;
		per_cpu_ptr(&rt_pull_head, i)->next = NULL;
	}
}


#if defined(CONFIG_ARM64) && defined(STACK_PROTECTOR)
#define NOP 0xd503201f
static void disable_stack_protector(void)
{
	int i;
	void *addr = __orig___schedule + STACK_PROTECTOR;

	for (i=0; i<STACK_PROTECTOR_LEN; i++, addr+=4)
		aarch64_insn_patch_text_nosync(addr, NOP);
}
#else
static void disable_stack_protector(void) { }
#endif

static int __sync_sched_install(void *arg)
{
	int error;

	if (is_first_process()) {
		stop_time_p0 = ktime_get();

		/* double checker simple memory pool */
		if ((error = recheck_smps()))
			atomic_cmpxchg(&global_error, 0, error);
	}

	error = stack_check(true);
	if (error)
		atomic_cmpxchg(&global_error, 0, error);
	atomic_dec(&cpu_finished);

	/* wait for all cpu to finish stack check */
	atomic_cond_read_relaxed(&cpu_finished, !VAL);

	if ((error = atomic_read(&global_error))) {
		print_error(error);
		atomic_dec(&clear_finished);
		atomic_dec(&redirect_finished);
		return error;
	}

	if (is_first_process()) {
		sched_alloc_extrapad();
		stop_time_p1 = ktime_get();
	}

	clear_sched_state(false);
	atomic_dec(&clear_finished);
	/* wait for all cpu to finish state rebuild */
	atomic_cond_read_relaxed(&clear_finished, !VAL);

	if (is_first_process()) {
		switch_sched_class(true);
		JUMP_OPERATION(install);
		disable_stack_protector();
		reset_balance_callback();
	}

	atomic_dec(&redirect_finished);
	atomic_cond_read_relaxed(&redirect_finished, !VAL);
	rebuild_sched_state(true);

	if (is_first_process())
		stop_time_p2 = ktime_get();

	return 0;
}

static int __sync_sched_restore(void *arg)
{
	int error;

	if (is_first_process())
		stop_time_p0 = ktime_get();

	error = stack_check(false);
	if (error)
		atomic_cmpxchg(&global_error, 0, error);
	atomic_dec(&cpu_finished);

	/* wait for all cpu to finish stack check */
	atomic_cond_read_relaxed(&cpu_finished, !VAL);

	if ((error = atomic_read(&global_error))) {
		print_error(error);
		atomic_dec(&clear_finished);
		atomic_dec(&redirect_finished);
		return error;
	}

	if (is_first_process())
		stop_time_p1 = ktime_get();

	clear_sched_state(true);
	atomic_dec(&clear_finished);
	/* wait for all cpu to finish state rebuild */
	atomic_cond_read_relaxed(&clear_finished, !VAL);

	if (is_first_process()) {
		switch_sched_class(false);
		JUMP_OPERATION(remove);
		reset_balance_callback();
	}

	atomic_dec(&redirect_finished);
	atomic_cond_read_relaxed(&redirect_finished, !VAL);
	rebuild_sched_state(false);

	if (is_first_process()) {
		sched_free_extrapad();
		stop_time_p2 = ktime_get();
	}

	return 0;
}

static int sync_sched_mod(void *func)
{
	int ret;
	ktime_t stop_start, stop_end;

	stop_start = ktime_get();
	ret = stop_machine(func, NULL, cpu_online_mask);
	stop_end = ktime_get();

	stop_time = ktime_to_ns(ktime_sub(stop_end, stop_start));
	return ret;
}

#ifdef CONFIG_SCHED_DEBUG
extern void __orig_register_sched_domain_sysctl(void);
extern void __orig_unregister_sched_domain_sysctl(void);

extern void __mod_register_sched_domain_sysctl(void);
extern void __mod_unregister_sched_domain_sysctl(void);

static inline void install_sched_domain_sysctl(void)
{
	mutex_lock(&cgroup_mutex);
	plugsched_cpuset_lock();

	__orig_unregister_sched_domain_sysctl();
	__mod_register_sched_domain_sysctl();

	plugsched_cpuset_unlock();
	mutex_unlock(&cgroup_mutex);
}

static inline void restore_sched_domain_sysctl(void)
{
	mutex_lock(&cgroup_mutex);
	plugsched_cpuset_lock();

	__mod_unregister_sched_domain_sysctl();
	cpumask_copy(sd_sysctl_cpus, cpu_possible_mask);
	__orig_register_sched_domain_sysctl();

	plugsched_cpuset_unlock();
	mutex_unlock(&cgroup_mutex);
}

/* sched_debug and sched_features interface in debugfs */
static struct dentry* find_dentry(const char* name)
{
	struct path f_path;

	kern_path(name, LOOKUP_FOLLOW, &f_path);

	return f_path.dentry;
}

void install_sched_debugfs(void)
{
	debugfs_remove(find_dentry("/sys/kernel/debug/sched_features"));

	sched_features_dir = debugfs_create_file("sched_features", 0644, NULL, NULL,
				&__mod_sched_feat_fops);
}

extern struct file_operations __orig_sched_feat_fops;
extern struct seq_operations  __orig_sched_debug_sops;

void restore_sched_debugfs(void)
{
	debugfs_remove(sched_features_dir);
	debugfs_create_file("sched_features", 0644, NULL, NULL, &__orig_sched_feat_fops);
}

/* sched_debug interface in proc */
int install_sched_debug_procfs(void)
{
	remove_proc_entry("sched_debug", NULL);

	if (!proc_create_seq("sched_debug", 0444, NULL, &__mod_sched_debug_sops))
		return -ENOMEM;

	return 0;
}

int restore_sched_debug_procfs(void)
{
	remove_proc_entry("sched_debug", NULL);

	if (!proc_create_seq("sched_debug", 0444, NULL, &__orig_sched_debug_sops))
		return -ENOMEM;

	return 0;
}
#endif

#ifdef CONFIG_SCHEDSTATS
extern struct seq_operations __orig_schedstat_sops;

/* schedstat interface in proc */
int install_proc_schedstat(void)
{
	remove_proc_entry("schedstat", NULL);

	if (!proc_create_seq("schedstat", 0444, NULL, &__mod_schedstat_sops))
		return -ENOMEM;

	return 0;
}

int restore_proc_schedstat(void)
{
	remove_proc_entry("schedstat", NULL);

	if (!proc_create_seq("schedstat", 0444, NULL, &__orig_schedstat_sops))
		return -ENOMEM;

	return 0;
}
#endif

static void report_cur_status(char *ops)
{
	printk("scheduler %s: %-25s %12d\n", ops, "current cpu number is", nr_cpu_ids);
	printk("scheduler %s: %-25s %12d\n", ops, "current thread number is", nr_threads);
}

static void report_detail_time(char *ops)
{
	report_cur_status(ops);
	printk("scheduler %s: %-25s %12lld ns\n", ops, "stop machine time is", stop_time);
	printk("scheduler %s: %-25s %12lld ns\n", ops, "stop handler time is",
			ktime_to_ns(ktime_sub(stop_time_p2, stop_time_p0)));
	printk("scheduler %s: %-25s %12lld ns\n", ops, "stack check time is",
			ktime_to_ns(ktime_sub(stop_time_p1, stop_time_p0)));
	printk("scheduler %s: %-25s %12lld ns\n", ops, "all the time is",
			ktime_to_ns(ktime_sub(main_end, main_start)));
}

static int load_sched_routine(void)
{
	int ret;

	/* Add refcnt to avoid rmmod before disable. */
	__module_get(THIS_MODULE);

	printk("scheduler module is loading\n");
	main_start = ktime_get();

	if (sched_mempools_create()) {
		printk("scheduler: Error: create mempools failed!\n");
		module_put(THIS_MODULE);
		return -ENOMEM;
	}

	cpu_maps_update_begin();
	parallel_state_check_init();
	process_id_init();

	ret = sync_sched_mod(__sync_sched_install);
	cpu_maps_update_done();
	if (ret) {
		sched_mempools_destroy();
		module_put(THIS_MODULE);
		return ret;
	}

#ifdef CONFIG_SCHEDSTATS
	install_proc_schedstat();
#endif
#ifdef CONFIG_SCHED_DEBUG
	install_sched_domain_sysctl();
	install_sched_debug_procfs();
	install_sched_debugfs();
#endif

	main_end = ktime_get();
	report_detail_time("load");
	scheduler_enable = 1;

	return 0;
}

static int unload_sched_routine(void)
{
	int ret;

	printk("scheduler module is unloading\n");
	main_start = ktime_get();

	cpu_maps_update_begin();
	parallel_state_check_init();
	process_id_init();

	ret = sync_sched_mod(__sync_sched_restore);
	cpu_maps_update_done();
	if (ret)
		return ret;

#ifdef CONFIG_SCHEDSTATS
	restore_proc_schedstat();
#endif
#ifdef CONFIG_SCHED_DEBUG
	restore_sched_domain_sysctl();
	restore_sched_debug_procfs();
	restore_sched_debugfs();
#endif

	sched_mempools_destroy();
	main_end = ktime_get();
	report_detail_time("unload");

	module_put(THIS_MODULE);
	scheduler_enable = 0;

	return 0;
}

static ssize_t plugsched_enabled_store(struct kobject *kobj,
		struct kobj_attribute *attr, const char *buf, size_t count)
{
	int ret;
	unsigned long val;

	ret = kstrtoul(buf, 10, &val);
	if (ret)
		return ret;

	val = !!val;

	if (scheduler_enable == val)
		return count;

	if (val)
		ret = load_sched_routine();
	else
		ret = unload_sched_routine();

	if (ret)
		return ret;

	return count;
}

static ssize_t plugsched_enable_show(struct kobject *kobj,
		struct kobj_attribute *attr, char *buf)
{
	return sprintf(buf, "%d\n", scheduler_enable);
}

static struct kobj_attribute plugsched_enable_attr =
	__ATTR(enable, 0644, plugsched_enable_show, plugsched_enabled_store);

static int register_plugsched_enable(void)
{
	int ret = -ENOMEM;

	plugsched_dir = kobject_create_and_add("plugsched", kernel_kobj);
	if (!plugsched_dir)
		return -ENOMEM;

	plugsched_subdir = kobject_create_and_add("plugsched", plugsched_dir);
	if (!plugsched_subdir)
		goto error;

	vmlinux_moddir = kobject_create_and_add("vmlinux", plugsched_subdir);
	if (!vmlinux_moddir)
		goto error;

	ret = sysfs_create_file(plugsched_subdir, &plugsched_enable_attr.attr);
	if (ret)
		goto error;

	return 0;

error:
	kobject_put(vmlinux_moddir);
	kobject_put(plugsched_subdir);
	kobject_put(plugsched_dir);
	return ret;
}

static void unregister_plugsched_enable(void)
{
	sysfs_remove_file(plugsched_subdir, &plugsched_enable_attr.attr);
	kobject_put(vmlinux_moddir);
	kobject_put(plugsched_subdir);
	kobject_put(plugsched_dir);
}

static int register_tainted_functions(void)
{
	struct tainted_function *tf;

	for (tf = tainted_functions; tf->name; tf++) {
		tf->kobj = kobject_create_and_add(tf->name, vmlinux_moddir);
		if (!tf->kobj)
			return -ENOMEM;
	}

	return 0;
}

static void unregister_tainted_functions(void)
{
	struct tainted_function *tf;

	for (tf = tainted_functions; tf->name; tf++) {
		if (!tf->kobj)
			return;
		kobject_put(tf->kobj);
	}
}

static inline void unregister_plugsched_sysfs(void)
{
	unregister_tainted_functions();
	unregister_plugsched_enable();
}

static int register_plugsched_sysfs(void)
{
	if (register_plugsched_enable()) {
		printk("scheduler: Error: Register plugsched sysfs failed!\n");
		return -ENOMEM;
	}

	if (register_tainted_functions()) {
		printk("scheduler: Error: Register taint functions failed!\n");
		unregister_plugsched_sysfs();
		return -ENOMEM;
	}

	return 0;
}

static int __init sched_mod_init(void)
{
	int ret;

	CHECK_STACK_LAYOUT();

	printk("Hi, scheduler mod is installing!\n");
	init_start = ktime_get();

	sched_springboard = (unsigned long)__orig___schedule + SPRINGBOARD;

	if (jump_init_all())
		return -EBUSY;

	/* This must after jump_init_all function !!! */
	stack_check_init();

	ret = register_plugsched_sysfs();
	if (ret)
		return ret;

	init_end = ktime_get();
	printk("scheduler: total initialization time is %14lld ns\n",
			ktime_to_ns(ktime_sub(init_end, init_start)));;

	ret = load_sched_routine();
	if (ret)
		unregister_plugsched_sysfs();

	return ret;
}

static void __exit sched_mod_exit(void)
{
	unregister_plugsched_sysfs();

	printk("Bye, scheduler mod has be removed!\n");
}

module_init(sched_mod_init);
module_exit(sched_mod_exit);
MODULE_LICENSE("GPL");
