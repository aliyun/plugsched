#include <linux/module.h>
#include <linux/init.h>
#include <linux/printk.h>
#include <linux/path.h>
#include <linux/mutex.h>
#include <linux/namei.h>
#include <linux/livepatch.h>
#include <linux/sched/task.h>
#include "../sched.h"
#include "helper.h"
#include "mempool.h"
#include "head_jump.h"
#include "stack_check.h"

#define RETRY_CNT 5
#define MAX_CPU_NR		1024

static int retry_count;

int process_id[MAX_CPU_NR];
atomic_t cpu_finished;
static atomic_t global_error;
static atomic_t redirect_done;

DECLARE_PER_CPU(struct callback_head, dl_push_head);
DECLARE_PER_CPU(struct callback_head, dl_pull_head);
DECLARE_PER_CPU(struct callback_head, rt_push_head);
DECLARE_PER_CPU(struct callback_head, rt_pull_head);
unsigned long sched_springboard;

extern struct mutex cgroup_mutex;
extern struct mutex cpuset_mutex;
extern cpumask_var_t sd_sysctl_cpus;
extern int __mod_sched_cpu_activate(unsigned int cpu);
extern int __mod_sched_cpu_deactivate(unsigned int cpu);
extern void __mod_run_rebalance_domains(struct softirq_action *h);
extern void run_rebalance_domains(struct softirq_action *h);
extern const struct file_operations sched_feat_fops;
extern const struct seq_operations sched_debug_sops;
extern const struct seq_operations schedstat_sops;
extern unsigned long sched_springboard;

static struct dentry *sched_features_dir;
static s64 stop_time;
ktime_t stop_time_p0, stop_time_p1, stop_time_p2;
ktime_t main_start, main_end, init_start, init_end;

extern void init_sched_rebuild(void);
extern void clear_sched_state(bool mod);
extern void rebuild_sched_state(bool mod);

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

static void print_error(int error, int retry_count)
{
	if (is_first_process()) {
		if (error == -ENOMEM) {
			printk("scheduler: Error: not enough memory for mempool! Retrying...X%d\n", retry_count);
		} else if(error == -EBUSY) {
			printk("scheduler: Error: Device or resources busy! Retrying...X%d\n",
					retry_count);
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

static inline void install_sched_domain_sysctl(void)
{
	void (*old_unregister_sd_sysctl)(void);

	mutex_lock(&cgroup_mutex);
	mutex_lock(&cpuset_mutex);

	old_unregister_sd_sysctl = (void *)kallsyms_lookup_name("unregister_sched_domain_sysctl");
	old_unregister_sd_sysctl();
	register_sched_domain_sysctl();

	mutex_unlock(&cpuset_mutex);
	mutex_unlock(&cgroup_mutex);
}

static inline void restore_sched_domain_sysctl(void)
{
	void (*old_register_sd_sysctl)(void);

	mutex_lock(&cgroup_mutex);
	mutex_lock(&cpuset_mutex);

	unregister_sched_domain_sysctl();
	cpumask_copy(sd_sysctl_cpus, cpu_possible_mask);
	old_register_sd_sysctl = (void *)kallsyms_lookup_name("register_sched_domain_sysctl");
	old_register_sd_sysctl();

	mutex_unlock(&cpuset_mutex);
	mutex_unlock(&cgroup_mutex);
}

static int __sync_sched_install(void *arg)
{
	int error;

	if (is_first_process()) {
		stop_time_p0 = ktime_get();

		/* double checker simple memory pool */
		if (error = recheck_smps())
			atomic_cmpxchg(&global_error, 0, error);
	}

	error = stack_check(true);
	atomic_dec(&cpu_finished);
	if (error)
		atomic_cmpxchg(&global_error, 0, error);

	/* wait for all cpu to finish stack check */
	atomic_cond_read_relaxed(&cpu_finished, !VAL);

	if (error = atomic_read(&global_error)) {
		print_error(error, retry_count);
		return error;
	}

	if (is_first_process())
		stop_time_p1 = ktime_get();

	clear_sched_state(false);

	if (is_first_process()) {
		JUMP_OPERATION(install);
		sched_alloc_extrapad();

		/* should call in stop machine context */
		open_softirq(SCHED_SOFTIRQ, __mod_run_rebalance_domains);
		reset_balance_callback();
		atomic_set(&redirect_done, 1);
	}

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
	atomic_dec(&cpu_finished);
	if (error)
		atomic_cmpxchg(&global_error, 0, error);

	/* wait for all cpu to finish stack check */
	atomic_cond_read_relaxed(&cpu_finished, !VAL);

	if (error = atomic_read(&global_error)) {
		print_error(error, ++retry_count);
		return error;
	}

	if (is_first_process())
		stop_time_p1 = ktime_get();

	clear_sched_state(true);

	if (is_first_process()) {
		JUMP_OPERATION(remove);

		/* should call in stop machine context */
		open_softirq(SCHED_SOFTIRQ, run_rebalance_domains);
		reset_balance_callback();
		sched_free_extrapad();
		atomic_set(&redirect_done, 1);
	}

	atomic_cond_read_relaxed(&redirect_done, VAL);
	rebuild_sched_state(false);

	if (is_first_process())
		stop_time_p2 = ktime_get();

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
				&sched_feat_fops);
}

void restore_sched_debugfs(void)
{
	struct file_operations *old_schedfeat_fops =
		(struct file_operations *)kallsyms_lookup_name("sched_feat_fops");

	debugfs_remove(sched_features_dir);
	debugfs_create_file("sched_features", 0644, NULL, NULL, old_schedfeat_fops);
}

/* sched_debug interface in proc */
int install_sched_debug_procfs(void)
{
	remove_proc_entry("sched_debug", NULL);

	if (!proc_create_seq("sched_debug", 0444, NULL, &sched_debug_sops))
		return -ENOMEM;

	return 0;
}

int restore_sched_debug_procfs(void)
{
	struct seq_operations* old_sched_debug_sops =
		(struct seq_operations *)kallsyms_lookup_name("sched_debug_sops");

	remove_proc_entry("sched_debug", NULL);

	if (!proc_create_seq("sched_debug", 0444, NULL, old_sched_debug_sops))
		return -ENOMEM;

	return 0;
}

/* schedstat interface in proc */
int install_proc_schedstat(void)
{
	remove_proc_entry("schedstat", NULL);

	if (!proc_create_seq("schedstat", 0444, NULL, &schedstat_sops))
		return -ENOMEM;

	return 0;
}

int restore_proc_schedstat(void)
{
	struct seq_operations* old_schedstat_sops =
		(struct seq_operations*)kallsyms_lookup_name("schedstat_sops");

	remove_proc_entry("schedstat", NULL);

	if (!proc_create_seq("schedstat", 0444, NULL, old_schedstat_sops))
		return -ENOMEM;

	return 0;
}

static void report_cur_status(char *ops)
{
	printk("scheduler %s: current cpu number is  %-15d\n", ops, nr_cpu_ids);
	printk("scheduler %s: current thread number is  %-15d\n", ops, nr_threads);
}

static void report_detail_time(char *ops)
{
	report_cur_status(ops);
	printk("scheduler %s: stop machine time is  %-15lld ns\n", ops, stop_time);
	printk("scheduler %s: stop handler time is  %-15lld ns\n", ops,
			ktime_to_ns(ktime_sub(stop_time_p2, stop_time_p0)));
	printk("scheduler %s: stack check time is   %-15lld ns\n", ops,
			ktime_to_ns(ktime_sub(stop_time_p1, stop_time_p0)));
	printk("scheduler %s: the %s time is        %-15lld ns\n", ops, ops,
			ktime_to_ns(ktime_sub(main_end, main_start)));
}

static int load_sched_routine(void)
{
	retry_count = 0;

	printk("scheduler: module is loading\n");
	main_start = ktime_get();

retry:
	if (retry_count == RETRY_CNT)
		return -EBUSY;
	retry_count++;

	if (sched_mempools_create()) {
		printk("scheduler: Error: create mempools failed! Retrying...X%d\n",
				retry_count);
		goto retry;
	}

	atomic_set(&cpu_finished, num_online_cpus());
	atomic_set(&global_error, 0);
	atomic_set(&redirect_done, 0);
	process_id_init();

	if (sync_sched_mod(__sync_sched_install)) {
		sched_mempools_destroy();

		cond_resched();
		goto retry;
	}

	install_sched_domain_sysctl();

	install_sched_debug_procfs();
	install_proc_schedstat();
	install_sched_debugfs();

	update_max_interval();
	sched_init_granularity();

	main_end = ktime_get();
	report_detail_time("load");

	return 0;
}

static void unload_sched_routine(void)
{
	retry_count = 0;

	printk("scheduler: module is unloading\n");
	main_start = ktime_get();

retry:
	atomic_set(&cpu_finished, num_online_cpus());
	atomic_set(&global_error, 0);
	atomic_set(&redirect_done, 0);
	process_id_init();

	if (sync_sched_mod(__sync_sched_restore)) {
		cond_resched();
		goto retry;
	}

	restore_sched_domain_sysctl();

	restore_sched_debug_procfs();
	restore_proc_schedstat();
	restore_sched_debugfs();

	main_end = ktime_get();
	report_detail_time("unload");
}

static int __init sched_mod_init(void)
{
	printk("Hi, scheduler mod is installing!\n");
	init_start = ktime_get();

	sched_springboard = kallsyms_lookup_name("__schedule") + SPRINGBOARD;

	init_sched_rebuild();
	jump_init_all();

	/* This must after jump_init_all function !!! */
	stack_check_init();

	init_end = ktime_get();
	printk("scheduler: total initialization time is %-15lld ns\n",
			ktime_to_ns(ktime_sub(init_end, init_start)));;

	return load_sched_routine();
}

static void __exit sched_mod_exit(void)
{
	unload_sched_routine();
	sched_mempools_destroy();

	printk("Bey, scheduler mod has be removed!\n");
}

module_init(sched_mod_init);
module_exit(sched_mod_exit);
MODULE_LICENSE("GPL");
