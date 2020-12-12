#include <linux/mutex.h>
#include <linux/mm_types.h>
#include <linux/memcontrol.h>
#include <linux/sched.h>
#include <linux/wait.h>
#include <linux/cpumask.h>
#include <linux/sysctl.h>

extern struct mutex jump_label_mutex;
extern struct mutex text_mutex;
extern struct mutex cfs_constraints_mutex;
extern struct mutex shares_mutex;
extern struct mutex sched_domains_mutex;
extern struct mutex vmap_purge_lock;
extern struct mutex cgroup_mutex;
extern struct mutex cpuset_mutex;
extern void cpu_maps_update_begin(void);
extern void cpu_maps_update_done(void);

DECLARE_PER_CPU(struct ftrace_ret_stack *, idle_ret_stack);

struct mutex *mutex_check_list[] = {
	&jump_label_mutex,
	&text_mutex,
	&cfs_constraints_mutex,
	&shares_mutex,
	&sched_domains_mutex,
	&vmap_purge_lock,
};

extern int ftrace_graph_active;
extern struct ctl_table_root sysctl_table_root;
extern int namecmp(const char *name1, int len1, const char *name2, int len2);

/* Called under sysctl_lock */
static struct ctl_table *find_entry(struct ctl_table_header **phead,
	struct ctl_dir *dir, const char *name, int namelen)
{
	struct ctl_table_header *head;
	struct ctl_table *entry;
	struct rb_node *node = dir->root.rb_node;

	while (node)
	{
		struct ctl_node *ctl_node;
		const char *procname;
		int cmp;

		ctl_node = rb_entry(node, struct ctl_node, node);
		head = ctl_node->header;
		entry = &head->ctl_table[ctl_node - head->node];
		procname = entry->procname;

		cmp = namecmp(name, namelen, procname, strlen(procname));
		if (cmp < 0)
			node = node->rb_left;
		else if (cmp > 0)
			node = node->rb_right;
		else {
			*phead = head;
			return entry;
		}
	}
	return NULL;
}

const char *sysctl_checker_list[] = {
	"numa_balancing",
	"sched_rt_period_us",
	"sched_rt_runtime_us",
	"sched_rr_timeslice_ms",
	"sched_tick_update_load",
	"sched_blocked_averages",
	"sched_schedstats",
	"sched_min_granularity_ns",
	"sched_latency_ns",
	"sched_wakeup_granularity_ns",
	NULL
};

static int stack_check(bool install, int *error)
{
	int cpu;
	struct mutex **mutex_checker;
	struct ctl_table_header *kern_header, *header;
	const char **sysctl_checker;
	struct ctl_dir *kern_ctl_dir;

	for (mutex_checker=mutex_check_list; *mutex_checker; mutex_checker++)
		if (!list_empty(&(*mutex_checker)->wait_list))
			goto fail;

	for_each_online_cpu(cpu) {
		if (ftrace_graph_active && !per_cpu(idle_ret_stack, cpu))
			goto fail;
	}

	find_entry(&kern_header, &sysctl_table_root.default_set.dir, "kernel", 6);
	kern_ctl_dir = container_of(kern_header, struct ctl_dir, header);

	for (sysctl_checker=sysctl_checker_list; *sysctl_checker; sysctl_checker++) {
		find_entry(&header, kern_ctl_dir, *sysctl_checker, strlen(*sysctl_checker));
		if (header->used > 0)
			goto fail;
	}

	*error = 0;
	return 0;
fail:
	*error = -EAGAIN;
	return 1;
}

static inline void stack_protect_open(void)
{
	/* Don't change the order of these locks! */
	cpu_maps_update_begin();
	mutex_lock(&cgroup_mutex);
	mutex_lock(&cpuset_mutex);
}

static inline void stack_protect_close(void)
{
	/* Don't change the order of these locks! */
	mutex_unlock(&cpuset_mutex);
	mutex_unlock(&cgroup_mutex);
	cpu_maps_update_done();
}

static inline void stack_check_insmod_init(void) {}
static inline void stack_check_rmmod_init(void) {}
