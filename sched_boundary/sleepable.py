import os
import re
import sys
import logging
import coloredlogs
from itertools import izip, groupby as _groupby
from yaml import load, dump, CLoader as Loader, CDumper as Dumper
from itertools import chain as _chain
chain = _chain.from_iterable
from multiprocessing import Pool, cpu_count

coloredlogs.install(level='INFO')

def mp_map(fn, lst):
    pool = Pool(cpu_count())
    res = pool.map(fn, lst)
    pool.close()
    pool.join()
    res = chain(res)
    return res

def groupby(it, grouper, selector, reducer):
    sorted_list = sorted(it, key=grouper)
    return dict((k, reducer(map(selector, v))) for k, v in _groupby(sorted_list, grouper))

if __name__ == '__main__':
    logging.info('Running')
    coloredlogs.install(level='INFO')
    mod_path = sys.argv[1]

with open(os.path.join(mod_path, 'sched_boundary_extract.yaml')) as f:
    config = load(f, Loader)
with open(os.path.join(mod_path, 'sched_boundary_stack_safe.yaml')) as f:
    edges = groupby(map(str.split, f.readlines()),
                    grouper=lambda (f, t, _, __):f,
                    selector=lambda (f, t, carry, tail):(t, (eval(tail), eval(carry))),
                    reducer=dict
                   )

# tail means A jump to B, not A calls B
near_tail = [
    ('scheduler_ipi', 'irq_exit'),
    ('sched_feat_open', 'single_open'),
    ('do_sched_yield', 'schedule'),
]

fixed = {
    # Sure
     'vprintk_emit'                                  :"it's an invalid path for scheduler"
    ,'panic'                                         :"we don't care"
    ,'mutex_lock(text_mutex)'                        :"as the name suggests"
    ,'mutex_lock(jump_label_mutex)'                  :"as the name suggests"
    ,'mutex_lock(cfs_constraints_mutex)'             :"as the name suggests"
    ,'mutex_lock(shares_mutex)'                      :"as the name suggests"
    ,'atomic_dec_and_mutex_lock(jump_label_mutex)'   :"as the name suggests"
    ,'cpu_cgroup_css_alloc'                          :"we've held cgroup_mutx"
    ,'partition_sched_domains'                       :"we checked sched_domains_mutex"
    ,'ftrace_graph_init_idle_task'                   :"we checked ftrace_graph_active && !per_cpu(idle_ret_stack, cpu)"
    ,'try_purge_vmap_area_lazy'                      :"we checked vmap_purge_lock"
    ,'watchdog'                                      :"it's a false-positive caused by duplicate static method name"
    ,'sched_cpu_deactivate'                          :"we've held cpu_maps_update_begin"
    ,'sched_proc_update_handler'                     :"we checked kern_table's refcount"
    ,'sysctl_blocked_averages'                       :"we checked kern_table's refcount"
    ,'sysctl_tick_update_load'                       :"we checked kern_table's refcount"
    ,'sysctl_schedstats'                             :"we checked kern_table's refcount"
    ,"sysctl_numa_balancing"                         :"we checked kern_table's refcount"
    ,"sched_rt_handler"	                             :"we checked kern_table's refcount"
    ,"sched_rr_handler"	                             :"we checked kern_table's refcount"
}

sleep_leaf = [
    'do_wait_for_common',
    '__schedule',
    # Not real leaf below, but to speed up
    'kmalloc',
    'kzalloc',
    'kcalloc',
]

if len(sys.argv) > 1 and sys.argv[1] == '--no-fix':
    fixed = {}
    near_tail = []

roots = set(config['function']['interface']) | set(config['function']['fn_ptr'])

def find_sleep_path(root):
    return dfs(root, {}, 0, True)

all_sched_functions = set(config['function']['insider'])  | \
                      set(config['function']['fn_ptr'])   | \
                      set(config['function']['interface'])

def dfs(u, visited, depth, all_tail):
    if u in sleep_leaf:
        if not all_tail:
            return [[u]]
        else:
            return []

    if depth > 20 or u not in edges or u in fixed:
        return []

    paths = []
    for v, (tail, carry) in edges[u].iteritems():
        #tail |= (u, v) in near_tail
        tail = (u, v) in near_tail
        if carry and '{}({})'.format(v, '.'.join(carry)) in fixed:
            continue
        u_in_sched = u in all_sched_functions
        desc_paths = dfs(v, visited, depth + 1, all_tail & (not u_in_sched or tail))
        paths += [[u] + path for path in desc_paths]
    return paths

logging.info('Start finding sleepable')
all_paths = mp_map(find_sleep_path, roots - {'__schedule'} - set(fixed))

fail = False

for f, t in near_tail:
    logging.info('%s calling %s nearly at the tail of %s', f, t, f)
    logging.warn("WARNING: Better not modify any logic after %s calls %s, (include those functions called by %s)", f, t, f)

for f, reason in fixed.items():
    logging.info('Fixed stack safe issue: %s.', f)
    if reason:
        logging.info('   Because %s', reason)

for path in all_paths:
    out = 'UNSAFE STACK: ' + path[0]
    for f, t in izip(path, path[1:]):
        tail, carry = edges[f][t]
        if tail or (f, t) in near_tail or f in all_sched_functions:
            out += ' -> {}'.format(t)
        else:
            out += ' =$> {}'.format(t)
    logging.error(out)
    fail = True

if fail is False:
    logging.info('OK')
else:
    raise Exception("Stack check failed")
