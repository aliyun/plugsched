#!/usr/bin/python2

# This script checks whether the symbols defined inside sched have the
# same name in outside sched object files.

from sh import awk, nm
import sys
import os
import logging
import coloredlogs
import subprocess
from itertools import chain as _chain
chain = _chain.from_iterable
from collections import defaultdict
from multiprocessing import Pool, cpu_count

from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    print >> sys.stderr, "WARNING: YAML CLoader is not presented, it can be slow."
    from yaml import Loader, Dumper

plugsched_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

omit_duplicy = {
    "print_cpu": "we already fixed in sched_boundary.py",
    "watchdog": "we already fixed in sched_boundary.py",
    "sd_init": "we already fixed in sched_boundary.py",
    "css_tg": "they must always be identical",
}

def find_syms(obj):
    lines = subprocess.check_output(['/usr/bin/dwarfdump', '-i', '-d', obj]).splitlines()
    record = []
    for line in lines:
        if not line.startswith('<1>'):
            continue
        if 'DW_TAG_subprogram' not in line and 'DW_TAG_variable' not in line:
            continue
        if 'DW_AT_name' not in line:
            continue
        if 'DW_AT_external<yes(1)>' in line:
            continue
        if 'DW_AT_name<__compiletime_assert_' in line:
            continue
        fields = line.split()
        name = fields[1]
        loc = fields[3]
        record.append((name[name.index('<')+1:name.rindex('>')], loc[:-1]))
    return record

def mp_map(fn, lst, loglevel):
    logging.getLogger().setLevel(loglevel)
    pool = Pool(cpu_count())
    res = pool.map(fn, lst)
    pool.close()
    pool.join()
    res = chain(res)
    logging.getLogger().setLevel(logging.INFO)
    return res

def run_test(mod_path, kernel_obj_path):
    coloredlogs.install(level='INFO')
    for omit, reason in omit_duplicy.iteritems():
        logging.warn("Omit %s because %s", omit, reason)
    with open(os.path.join(mod_path, 'sched_boundary_extract.yaml')) as f:
        config = load(f, Loader)
    logging.info('Checking plugsched.ko symbols used by vmlinux. And ensure no duplicate versions of those symbols')

    # Find all possible kallsyms
    all_files = [os.path.join(kernel_obj_path, 'vmlinux')]
    for root, dirs, files in os.walk(kernel_obj_path):
        for f in files:
            f = os.path.join(root, f)
            if f.endswith('.ko.debug'):
                all_files.append(f)
            if f.endswith('.ko'):
                all_files.append(f)

    all_syms = set(mp_map(find_syms, all_files, logging.WARNING))

    external_syms = set(config['function']['interface']) | \
                    set(config['function']['fn_ptr']) | \
                    set(awk(nm(os.path.join(mod_path, 'kernel/sched/mod/plugsched.ko'), undefined_only=True, portability=True),
                            '$1 !~ /^\.LC/ {print $1}').splitlines())

    external_sym_files = defaultdict(list)
    for sym, fn in all_syms:
        if sym in external_syms:
            external_sym_files[sym].append(fn)

    error = False
    for external_sym, files in external_sym_files.iteritems():
        if len(files) > 1 and func not in omit_duplicy:
            error = True
            logging.error('External symbol %s appeared in these files: %s', sched_sym, ' '.join(files))

    if error:
        raise Exception("Checking vmlinux symbols faild.")
    logging.info("Success")

if __name__ == '__main__':
    run_test(sys.argv[1])
