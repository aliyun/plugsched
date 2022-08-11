# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
from itertools import islice as skipline
from itertools import chain as _chain
from sh import readelf
import json
import os
import copy
import sys
chain = _chain.from_iterable

config = None

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
                path = os.path.join(r, file)
                assert path.startswith('./')
                yield path[2:]

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
        if (fn, file) in func_class.fn:
            return file
    return None

def find_in_vmlinux(vmlinux_elf):
    # store sympos for local functions in module files
    local_sympos = {}
    # store exported function symbol (EXPORT_SYMBOL, EXPORT_SYMBOL_GPL)
    export_func = set()
    mangled = set()
    in_vmlinux = set()
    fn_pos = {}
    for line in skipline(readelf(vmlinux_elf, syms=True, wide=True, _iter=True), 3, None):
        fields = line.split()
        if len(fields) != 8: continue
        symtype, scope, key = fields[3], fields[4], fields[7]

        if symtype == 'FILE':
            filename = key
            # Disagreement 1:
            if filename in config.fullname:
                filename = config.fullname[filename]
            continue
        elif symtype == 'NOTYPE':
            # find exported function symbol (EXPORT_SYMBOL)
            if key.startswith('__ksymtab_') and filename in config.mod_files:
                key = key[len('__ksymtab_'):]
                file = get_in_any(key, config.mod_files)
                if file: export_func.add((key, file))
            continue
        elif symtype != 'FUNC':
            continue

        file = filename
        # Disagreement 4
        if '.' in key:
            # If function A has at least one mangled version, eg. A.isra, then function A
            # may be called through the mangled one. But A.cold doesn't lead to this problem
            # because A.cold is only called by A.
            if '.cold' not in key:
                mangled.add((key[:key.index('.')], file))
            continue

        if scope == 'LOCAL':
            fn_pos[key] = fn_pos.get(key, 0) + 1
            if filename not in config.all_files:
                continue

            # Disagreement 2
            if (key, filename) not in func_class.fn:
                file = get_in_any(key, config.mod_hdrs)
                if file is None: continue

            local_sympos[(key, file)] = fn_pos[key]
        else:
            # Disagreement 3
            file = get_in_any(key, config.all_files)
            if file is None: continue

        in_vmlinux.add((key, file))

    return {
        'in_vmlinux': in_vmlinux,
        'mangled': mangled,
        'local_sympos': local_sympos,
        'export': export_func
    }

# __insiders is a global variable only used by these two functions
__insiders = None

def inflect_one(edge):
    to_sym = tuple(edge['to'])
    if to_sym in __insiders:
        from_sym = tuple(edge['from'])
        if from_sym not in __insiders and \
           from_sym not in func_class.border and \
           from_sym not in func_class.init and \
           from_sym not in func_class.sidecar:
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
    # Returns None if function is a gcc built-in or assembly function
    name, file = name_and_file
    file = global_fn_dict.get(name, None) if file == '?' else file
    return (name, file) if file else None

def sidecar_inflect(sidecar, in_vmlinux):
    assert not (sidecar - in_vmlinux), \
            'sidecar functions should not be optimzied by GCC'

    leftover = set()
    for sym in sidecar:
        meta = metas_by_name[sym[1] + '.boundary']
        sidecar_dfs(meta, sym, in_vmlinux, leftover)

    return leftover

def sidecar_dfs(meta, start_sym, in_vmlinux, leftover):
    if start_sym in leftover: return

    leftover.add(start_sym)

    for edge in meta['edge']:
        if edge['to'] is None:
            continue
        from_sym = tuple(edge['from'])
        to_sym = tuple(edge['to'])
        if from_sym == start_sym and \
                to_sym[1] == start_sym[1] and \
                to_sym not in in_vmlinux:
            sidecar_dfs(meta, to_sym, in_vmlinux, leftover)

def check_redirect_mangled(f, meta):
    for edge in meta['edge']:
        if edge['to'] is None:
            continue
        from_sym = tuple(edge['from'])
        to_sym = tuple(edge['to'])
        # When caller and callee are not in the same file,
        # it should always be safe, because Linux doesn't do LTO
        if to_sym != f or to_sym[1] != from_sym[1]:
            continue
        # Unsafe if the caller is a sched_outsider
        if from_sym in func_class.sched_outsider:
            return True
        # If the caller is optimized too, check if it's unsafe recursively
        if from_sym in func_class.mangled or from_sym not in func_class.in_vmlinux:
            if check_redirect_mangled(from_sym, meta):
                return True
    return False

def func_class_arithmetics(fns):
    fns.callback -= fns.interface
    fns.callback_optimized = fns.callback - fns.in_vmlinux
    fns.callback -= fns.callback_optimized
    fns.border = fns.interface | fns.callback
    # exported function maybe used by kernel modules, it can't be internal function
    fns.initial_insider = fns.mod_fns - fns.border - fns.export

    # calc sidecar extraction functions
    fns.sidecar = set(config.sidecar)
    fns.sdcr_left = sidecar_inflect(fns.sidecar, fns.in_vmlinux)
    fns.sdcr_out = fns.sdcr_fns - fns.sdcr_left

    # Inflect outsider functions
    fns.insider = inflect(fns.initial_insider, edges) - fns.init
    fns.sched_outsider = (fns.mod_fns - fns.insider - fns.border) | fns.callback_optimized
    fns.optimized_out = fns.sched_outsider - fns.in_vmlinux - fns.init
    fns.public_user = fns.fn - fns.insider - fns.border
    fns.tainted = (fns.border | fns.insider | fns.sidecar) & fns.in_vmlinux
    fns.undefined = (fns.sched_outsider - fns.optimized_out) | fns.border | fns.sidecar

class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

if __name__ == '__main__':
    vmlinux = sys.argv[1]
    tmpdir = sys.argv[2]
    modpath = sys.argv[3]

    config = dotdict(read_config())
    config.mod_hdrs  = [f for f in config.mod_files if f.endswith('.h')]
    config.mod_srcs  = [f for f in config.mod_files if f.endswith('.c')]
    config.sidecar   = set() if config.sidecar is None else config.sidecar
    config.sdcr_srcs = [f[1] for f in config.sidecar]
    config.all_files = config.mod_hdrs + config.mod_srcs + config.sdcr_srcs
    config.fullname  = {os.path.basename(f):f for f in config.all_files}

    metas = []
    metas_by_name = {}
    for file in all_meta_files():
        meta = read_meta(file)
        metas.append(meta)
        metas_by_name[file] = meta

    func_class = dotdict({
        'fn':        set(),
        'init':      set(),
        'interface': set(),
        'callback':  set(),
        'mod_fns':   set(),
        'sdcr_fns':  set(),
    })

    edges = []
    decls = {}
    hdr_sym = {'fn':list(), 'var':list()}

    # first pass: calc init and interface set
    for meta in metas:
        for fn in meta['fn']:
            fn = dotdict(fn)
            fn.signature = tuple(fn.signature)
            func_class.fn.add(fn.signature)
            if fn.file in config.mod_files:
                func_class.mod_fns.add(fn.signature)
                decls[fn.signature] = fn.decl_str
            if fn.file in config.sdcr_srcs:
                func_class.sdcr_fns.add(fn.signature)
                decls[fn.signature] = fn.decl_str

            if fn.file in config.mod_hdrs: hdr_sym['fn'].append(fn)
            if fn.init: func_class.init.add(fn.signature)
            if fn.publ: global_fn_dict[fn.name] = fn.file

        for fn in meta['interface']:
            func_class.interface.add(tuple(fn))

    # second pass: fix vague filename, calc callback and edge set
    for meta in metas:
        for callback in meta['callback']:
            callback = lookup_if_global(callback)
            if callback and callback[1] in config.mod_files:
                func_class.callback.add(callback)

        for edge in meta['edge']:
            edge['to'] = lookup_if_global(edge['to'])
            if edge['to']:
                edges.append(edge)

    vmlinux_info = find_in_vmlinux(vmlinux)
    local_sympos = vmlinux_info['local_sympos']
    func_class.in_vmlinux = vmlinux_info['in_vmlinux']
    func_class.mangled = vmlinux_info['mangled']
    func_class.export = vmlinux_info['export']
    func_class_arithmetics(func_class)

    classes_out = [
        'sched_outsider', 'callback', 'interface', 'init', 'insider',
        'optimized_out', 'export', 'sdcr_out'
    ]
    for output_item in classes_out:
        config.function[output_item] = func_class[output_item]

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
                p_user = set(map(tuple, users)) & func_class.public_user
                if p_user:
                    user_set |= p_user
                    field_set.add(field)

        struct_properties[struct]['all_fields'] = all_set
        struct_properties[struct]['public_fields'] = field_set
        struct_properties[struct]['public_users'] = user_set

    # Sanity checks
    for sym in (func_class.sidecar | func_class.border) & func_class.mangled:
        meta = metas_by_name[sym[1] + '.boundary']
        assert not check_redirect_mangled(sym, meta), \
                "trying to redirect the mangled function %s (%s)" % sym
    assert not struct_properties['sched_class']['public_users'], \
            'struct sched_class should be purely private'

    with open(tmpdir + 'header_symbol.json', 'w') as f:
        json.dump(hdr_sym, f, indent=4)
    with open(tmpdir + 'boundary_doc.yaml', 'w') as f:
        dump(struct_properties, f, Dumper)
    with open(tmpdir + 'boundary_extract.yaml', 'w') as f:
        dump(dict(config), f, Dumper)

    tnt_fmt = 'TAINTED_FUNCTION({},{})'
    und_fmt = '"{}", {}'
    cb_fmt = "EXPORT_CALLBACK({fn}, {ret}, {params})"
    export = "EXPORT_PLUGSCHED({fn}, {ret}, {params})"
    mod_fmt = '__mod_{}'
    unds, taints = [], []
    for fn in func_class.undefined:
        unds.append(und_fmt.format(fn[0], local_sympos.get(fn, 0)))
    # Consistent with kpatch and livepatch: set global symbol's sympos to 1 in sysfs
    for fn in func_class.tainted:
        taints.append(tnt_fmt.format(fn[0], local_sympos.get(fn, 0) or 1))

    with open(modpath + 'tainted_functions.h', 'w') as f:
        f.write('\n'.join(taints))
    with open(tmpdir + 'symbol_resolve/undefined_functions.h', 'w') as f:
        f.write('{%s}' % '},\n{'.join(unds))
    with open(modpath + 'export_jump.h', 'w') as f:
        f.write('\n'.join([cb_fmt.format(**decls[fn]) for fn in func_class.callback]) + '\n')
        f.write('\n'.join([export.format(**decls[fn]) for fn in func_class.interface]) + '\n')
        f.write('\n'.join([export.format(**decls[fn]) for fn in func_class.sidecar]))
