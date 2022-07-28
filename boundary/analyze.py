# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
from itertools import islice as skipline
from itertools import chain as _chain
from sh import readelf
import logging
import json
import os
import copy
import sys
chain = _chain.from_iterable

config = None
# store sympos for local functions in module files
local_sympos = {}

# store exported function symbol (EXPORT_SYMBOL, EXPORT_SYMBOL_GPL)
export_func = set()

# tmp directory to store middle files
tmpdir = None

# directory to store schedule module source code
modpath = None

# Use set as the default sequencer for yaml
Loader.add_constructor(resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
                       lambda loader, node: set(loader.construct_sequence(node)))
Dumper.add_representer(set, lambda dumper, node: dumper.represent_list(node))
Dumper.add_representer(str,
                       lambda dumper, data: dumper.represent_scalar(u'tag:yaml.org,2002:str', data))

def read_config():
    with open(tmpdir + 'boundary.yaml') as f:
        return load(f, Loader)

def all_meta_files():
    for r, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.boundary'):
                yield os.path.join(r, file)

def read_meta(filename):
    with open(filename) as f:
        return json.load(f)

# This method connects gcc-plugin with vmlinux (or the ld linker)
# It serves two purposes right now:
#   1. find functions in vmlinux, to calc optimized_out later
#   2. find sympos from vmlinux, which will be used to check confliction with kpatch
# This must be called after we have read all files, and all vagueness has been solved.
#
# Four pitfalls because of disagreement between vmlinux and gcc-plugin, illustrated with examples
#
# Disagreement 1: vmlinux thinks XXX is in core.c, plugsched thinks it's in kernel/sched/core.c
# Disagreement 2: vmlinux thinks XXX is in core.c, plugsched thinks it's in sched.h
# Disagreement 3: vmlinux thinks XXX is in usercopy_64.c, plugsched thinks it's in core.c
# Disagrement: 4: vmlinux optimizes XXX to XXX.isra.1, XXX.constprop.1, etc. plugsched remains XXX.

def get_in_any(fn, files):
    for file in files:
        if (fn, file) in func_class['fn']:
            return file
    return None

def find_in_vmlinux(vmlinux_elf):
    in_vmlinux = set()
    fn_pos = {}
    for line in skipline(readelf(vmlinux_elf, syms=True, wide=True, _iter=True), 3, None):
        fields = line.split()
        if len(fields) != 8: continue
        symtype, scope, key = fields[3], fields[4], fields[7]

        if symtype == 'FILE':
            filename = key
            # Disagreement 1:
            if filename in config['fullname']:
                filename = config['fullname'][filename]
            continue
        elif symtype == 'NOTYPE':
            # find exported function symbol (EXPORT_SYMBOL)
            if key.startswith('__ksymtab_') and filename in config['mod_files']:
                key = key[len('__ksymtab_'):]
                file = get_in_any(key, config['mod_files'])
                if file: export_func.add((key, file))
            continue
        elif symtype != 'FUNC':
            continue

        file = filename
        # Disagreement 4
        if '.' in key: continue

        if scope == 'LOCAL':
            fn_pos[key] = fn_pos.get(key, 0) + 1
            if filename not in config['all_files']:
                continue

            # Disagreement 2
            if (key, filename) not in func_class['fn']:
                file = get_in_any(key, config['mod_hdrs'])
                if file is None: continue

            # Avoid potential bugs that sympos gets overwritten in the future.
            assert (key, file) not in local_sympos
            local_sympos[(key, file)] = fn_pos[key]
        else:
            # Disagreement 3
            file = get_in_any(key, config['all_files'])
            if file is None: continue

        in_vmlinux.add((key, file))

    return in_vmlinux

# __insiders is a global variable only used by these two functions
__insiders = None

def inflect_one(edge):
    to_sym = tuple(edge['to'])
    if to_sym in __insiders:
        from_sym = tuple(edge['from'])
        if from_sym not in __insiders and \
           from_sym not in func_class['border'] and \
           from_sym not in func_class['init'] and \
           from_sym not in func_class['sidecar']:
            return to_sym
    return None

def inflect(initial_insiders, edges):
    global __insiders
    __insiders = copy.deepcopy(initial_insiders)
    while True:
        delete_insider = list(filter(None, list(map(inflect_one, edges))))
        if not delete_insider:
            break
        __insiders -= set(delete_insider)
    return __insiders

global_fn_dict = {}
def lookup_if_global(name_and_file):
    # Returns None if function is a gcc built-in function
    name, file = name_and_file
    file = global_fn_dict.get(name, None) if file == '?' else file
    return (name, file) if file else None

def sidecar_inflect(sidecar, in_vmlinux):
    assert not (sidecar - in_vmlinux), \
            'sidecar functions should not be optimzied by GCC'

    leftover = set()
    for sym in sidecar:
        meta = read_meta(sym[1] + '.boundary')
        sidecar_dfs(meta, sym, in_vmlinux, leftover)

    return leftover

def sidecar_dfs(meta, start_sym, in_vmlinux, leftover):
    if start_sym in leftover: return

    leftover.add(start_sym)

    for edge in meta['edge']:
        from_sym = tuple(edge['from'])
        to_sym = tuple(edge['to'])
        if from_sym == start_sym and \
                to_sym[1] == start_sym[1] and \
                to_sym not in in_vmlinux:
            sidecar_dfs(meta, to_sym, in_vmlinux, leftover)

if __name__ == '__main__':
    vmlinux = sys.argv[1]
    tmpdir = sys.argv[2]
    modpath = sys.argv[3]

    config = read_config()
    config['mod_hdrs']  = [f for f in config['mod_files'] if f.endswith('.h')]
    config['mod_srcs']  = [f for f in config['mod_files'] if f.endswith('.c')]
    config['sidecar']   = set() if config['sidecar'] is None else config['sidecar']
    config['sdcr_srcs'] = [f[1] for f in config['sidecar']]
    config['all_files'] = config['mod_hdrs'] + config['mod_srcs'] + config['sdcr_srcs']
    config['fullname']  = {os.path.basename(f):f for f in config['all_files']}
    metas = list(map(read_meta, all_meta_files()))

    func_class = {
        'fn':        set(),
        'init':      set(),
        'interface': set(),
        'fn_ptr':    set(),
        'mod_fns':   set(),
        'sdcr_fns':  set(),
    }

    edges = []
    decls = {}
    hdr_sym = {'fn':list(), 'var':list()}

    # first pass: calc init and interface set
    for meta in metas:
        for fn in meta['fn']:
            init, publ, signature, file, name = fn['init'], fn['public'], tuple(fn['signature']), fn['file'], fn['name']
            func_class['fn'].add(signature)

            if file in config['mod_files']:
                func_class['mod_fns'].add(signature)
                decls[signature] = fn['decl_str']
            if file in config['sdcr_srcs']:
                func_class['sdcr_fns'].add(signature)
                decls[signature] = fn['decl_str']

            if file in config['mod_hdrs']: hdr_sym['fn'].append(fn)
            if init: func_class['init'].add(signature)
            if publ: global_fn_dict[name] = file

        for fn in meta['interface']:
            func_class['interface'].add(tuple(fn))

    # second pass: fix vague filename, calc fn_ptr and edge set
    for meta in metas:
        for fn_ptr in meta['fn_ptr']:
            fn_ptr = lookup_if_global(fn_ptr)
            if fn_ptr and fn_ptr[1] in config['mod_files']:
                func_class['fn_ptr'].add(fn_ptr)

        for edge in meta['edge']:
            edge['to'] = lookup_if_global(edge['to'])
            if edge['to']:
                edges.append(edge)

    func_class['in_vmlinux'] = find_in_vmlinux(vmlinux)
    func_class['export'] = export_func
    func_class['fn_ptr'] -= func_class['interface']
    func_class['fn_ptr_optimized'] = func_class['fn_ptr'] - func_class['in_vmlinux']
    func_class['fn_ptr'] -= func_class['fn_ptr_optimized']
    func_class['border'] = func_class['interface'] | func_class['fn_ptr']
    # exported function maybe used by kernel modules, it can't be internal function
    func_class['initial_insider'] = func_class['mod_fns'] - func_class['border'] - func_class['export']

    # calc sidecar extraction functions
    func_class['sidecar'] = set(config['sidecar'])
    func_class['sdcr_left'] = sidecar_inflect(func_class['sidecar'], func_class['in_vmlinux'])
    func_class['sdcr_out'] = func_class['sdcr_fns'] - func_class['sdcr_left']

    # Inflect outsider functions
    func_class['insider'] = inflect(func_class['initial_insider'], edges) - func_class['init']
    func_class['sched_outsider'] = (func_class['mod_fns'] - func_class['insider'] - func_class['border']) | func_class['fn_ptr_optimized']
    func_class['optimized_out'] = func_class['sched_outsider'] - func_class['in_vmlinux'] - func_class['init']
    func_class['public_user'] = func_class['fn'] - func_class['insider'] - func_class['border']
    func_class['tainted'] = (func_class['border'] | func_class['insider'] | func_class['sidecar']) & func_class['in_vmlinux']
    func_class['undefined'] = func_class['sched_outsider'] | func_class['border'] | func_class['sidecar']

    for output_item in ['sched_outsider', 'fn_ptr', 'interface', 'init', 'insider', 'optimized_out', 'export', 'sdcr_out']:
        config['function'][output_item] = func_class[output_item]

    # Handle Struct public fields. The right hand side gives an example
    struct_properties = dict()
    for struct in set(chain(m['struct'].keys() for m in metas)):
        struct_properties[struct] = dict()
        all_set = set()
        field_set = set()
        user_set = set()

        for m in metas:
            if struct not in m['struct']: continue
            all_set |= set(m['struct'][struct]['all_fields'])

            for field, users in m['struct'][struct]['public_fields'].items():
                p_user = set(map(tuple, users)) & func_class['public_user']
                if p_user:
                    user_set |= p_user
                    field_set.add(field)

        struct_properties[struct]['all_fields'] = all_set
        struct_properties[struct]['public_fields'] = field_set
        struct_properties[struct]['public_users'] = user_set

    with open(tmpdir + 'header_symbol.json', 'w') as f:
        json.dump(hdr_sym, f, indent=4)
    with open(tmpdir + 'boundary_doc.yaml', 'w') as f:
        dump(struct_properties, f, Dumper)
    with open(tmpdir + 'boundary_extract.yaml', 'w') as f:
        dump(config, f, Dumper)
    with open(modpath + 'tainted_functions.h', 'w') as f:
        # Consistent with kpatch and livepatch: set global symbol's sympos to 1 in sysfs
        f.write('\n'.join(["TAINTED_FUNCTION({fn},{val})".format(fn=fn[0], val=local_sympos.get(fn, 0) \
                if local_sympos.get(fn, 0) else 1) for fn in func_class['tainted']]))
    with open(tmpdir + 'symbol_resolve/undefined_functions.h', 'w') as f:
        array = '},\n{'.join(['"{fn}", {sympos}'.format(fn=fn[0], sympos=local_sympos.get(fn, 0)) \
                for fn in func_class['undefined']])
        f.write('{%s}' % array)
    with open(tmpdir + 'interface_fn_ptrs', 'w') as f:
        f.write('\n'.join([fn[0] for fn in func_class['interface'] | func_class['sidecar']]) + '\n')
        f.write('\n'.join(['__mod_' + fn[0] for fn in config['function']['fn_ptr']]))
    with open(modpath + 'export_jump.h', 'w') as f:
        fn_ptr_export_fmt = "PLUGSCHED_FN_PTR({fn}, {ret}, {params})"
        export_fmt = "EXPORT_PLUGSCHED({fn}, {ret}, {params})"
        f.write('\n'.join([fn_ptr_export_fmt.format(**decls[fn]) for fn in func_class['fn_ptr']]) + '\n')
        f.write('\n'.join([export_fmt.format(**decls[fn]) for fn in func_class['interface']]) + '\n')
        f.write('\n'.join([export_fmt.format(**decls[fn]) for fn in func_class['sidecar']]))
