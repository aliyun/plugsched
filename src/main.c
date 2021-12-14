#include <linux/module.h>
#include <linux/init.h>
#include <linux/printk.h>
#include <linux/path.h>
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

unsigned int process_id[MAX_CPU_NR];
atomic_t check_result = ATOMIC_INIT(1);

DECLARE_PER_CPU(struct callback_head, dl_push_head);
DECLARE_PER_CPU(struct callback_head, dl_pull_head);
DECLARE_PER_CPU(struct callback_head, rt_push_head);
DECLARE_PER_CPU(struct callback_head, rt_pull_head);
unsigned long sched_springboard;

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
ktime_t main_time_p0, main_time_p1, main_time_p2;

extern void init_sched_rebuild(void);
extern void clear_sched_state(bool mod);
extern void rebuild_sched_state(bool mod);

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

	old_unregister_sd_sysctl = (void *)kallsyms_lookup_name("unregister_sched_domain_sysctl");
	old_unregister_sd_sysctl();
	register_sched_domain_sysctl();
}

static inline void restore_sched_domain_sysctl(void)
{
	void (*old_register_sd_sysctl)(void);

	unregister_sched_domain_sysctl();
	cpumask_copy(sd_sysctl_cpus, cpu_possible_mask);
	old_register_sd_sysctl = (void *)kallsyms_lookup_name("register_sched_domain_sysctl");
	old_register_sd_sysctl();
}

static int __sync_sched_install(void *arg)
{
	int error;

	if (!stop_time_p0)
		stop_time_p0 = ktime_get();

	if (stack_check(true, &error)) {
		if (error) {
			printk("plugsched: Error: Device or resources busy! Retrying...X%d\n",
					retry_count);
			return error;
		}
		goto rebuild;
	}

	if (recheck_smps()) {
		printk("plugsched: Error: not enough memory for mempool! Retrying...X%d\n",
				retry_count);
		return -ENOMEM;
	}

	clear_sched_state(false);
	JUMP_OPERATION(install);

	sched_alloc_extrapad();

	/* should call in stop machine context */
	open_softirq(SCHED_SOFTIRQ, __mod_run_rebalance_domains);
	reset_balance_callback();

rebuild:
	rebuild_sched_state(true);
	stop_time_p2 = ktime_get();
	return 0;
}

static int __sync_sched_restore(void *arg)
{
	int error;

	if (!stop_time_p0)
		stop_time_p0 = ktime_get();

	if (stack_check(false, &error)) {
		if (error) {
			printk("plugsched: Warning: Device or resources busy! Retrying...X%d\n",
					++retry_count);
			return error;
		}
		goto rebuild;
	}
	clear_sched_state(true);

	JUMP_OPERATION(remove);

	/* should call in stop machine context */
	open_softirq(SCHED_SOFTIRQ, run_rebalance_domains);
	reset_balance_callback();
	sched_free_extrapad();

rebuild:
	rebuild_sched_state(false);

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
	printk("plugsched %s: current cpu number is  %-15d ns\n", ops, nr_cpu_ids);
	printk("plugsched %s: current thread number is  %-15d ns\n", ops, nr_threads);
}

static void report_detail_time(char *ops)
{
	report_cur_status(ops);
	printk("plugsched %s: stop machine time is  %-15lld ns\n", ops, stop_time);
	printk("plugsched %s: stop handler time is  %-15lld ns\n", ops,
			ktime_to_ns(ktime_sub(stop_time_p2, stop_time_p0)));
	printk("plugsched %s: stack check time is   %-15lld ns\n", ops,
			ktime_to_ns(ktime_sub(stop_time_p1, stop_time_p0)));

	printk("plugsched %s: %s init time is       %-15lld ns\n", ops, ops,
			ktime_to_ns(ktime_sub(main_time_p1, main_time_p0)));
	printk("plugsched %s: %s all time is        %-15lld ns\n", ops, ops,
			ktime_to_ns(ktime_sub(main_time_p2, main_time_p0)));
	stop_time_p0 = 0;
}

static int __init sched_mod_init(void)
{
	retry_count = 0;

	printk("Hi, plugsched mod is loading\n");

	main_time_p0 = ktime_get();
	sched_springboard = kallsyms_lookup_name("__schedule") + SPRINGBOARD;

	init_sched_rebuild();

	jump_init_all();
	/* This must after jump_init_all function !!! */
	stack_check_insmod_init();
	main_time_p1 = ktime_get();

retry:
	if (retry_count == RETRY_CNT)
		return -EBUSY;
	retry_count++;

	if (sched_mempools_create()) {
		printk("plugsched: Error: create mempools failed! Retrying...X%d\n",
				retry_count);
		goto retry;
	}

	stack_protect_open();

	if (sync_sched_mod(__sync_sched_install)) {
		stack_protect_close();
		sched_mempools_destroy();

		cond_resched();
		goto retry;
	}

	install_sched_domain_sysctl();
	stack_protect_close();

	install_sched_debug_procfs();
	install_proc_schedstat();
	install_sched_debugfs();

	update_max_interval();
	sched_init_granularity();
	main_time_p2 = ktime_get();

	report_detail_time("install");

	return 0;
}

static void __exit sched_mod_exit(void)
{
	retry_count = 0;

	printk("Bye, plugsched mod is unloading\n");

	main_time_p0 = ktime_get();
	stack_check_rmmod_init();

	main_time_p1 = ktime_get();
retry:
	stack_protect_open();

	if (sync_sched_mod(__sync_sched_restore)) {
		stack_protect_close();
		cond_resched();
		goto retry;
	}

	restore_sched_domain_sysctl();
	stack_protect_close();

	restore_sched_debug_procfs();
	restore_proc_schedstat();
	restore_sched_debugfs();
	sched_mempools_destroy();

	main_time_p2 = ktime_get();
	report_detail_time("remove");
}


module_init(sched_mod_init);
module_exit(sched_mod_exit);
MODULE_LICENSE("GPL");
