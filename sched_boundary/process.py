from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
from itertools import islice as skipline, groupby as _groupby
from itertools import chain as _chain
from collections import Counter
from sh import readelf
import logging
import json
import os
chain = _chain.from_iterable

fn_symbol_classify = {}
config = None

# Use set as the default sequencer for yaml
Loader.add_constructor(resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
                       lambda loader, node: set(loader.construct_sequence(node)))
Dumper.add_representer(set, lambda dumper, node: dumper.represent_list(node))
Dumper.add_representer(unicode,
                       lambda dumper, data: dumper.represent_scalar(u'tag:yaml.org,2002:str', data))

class Symbol(object):
    objs = {}
    pos = Counter()

    @classmethod
    def get(cls, desc, no_create=False):
        name = desc['name']
        file = desc['file']
        if name in cls.objs and file in cls.objs[name]:
            return cls.objs[name][file]
        if no_create:
            return None
        else:
            new_obj = cls(name, file)
            cls.objs.setdefault(name, dict())[file] = new_obj
            return new_obj

    @classmethod
    def get_in_any(cls, desc, files):
        name = desc['name']
        if name not in cls.objs:
            # Mainly because gcc-plugin doesn't work asm files
            return 0, None
        files_cur = set(cls.objs[name].keys()) & set(files)
        if len(files_cur) == 1:
            return 1, cls.objs[name][files_cur.pop()]
        elif len(files_cur) == 0:
            return 0, None
        return 2, None

    @classmethod
    def inc_pos(cls, name):
        Symbol.pos[name] += 1

    @classmethod
    def assign_cur_pos(cls, obj):
        obj.pos = Symbol.pos[obj.name]

    @classmethod
    def assign_pos(cls, obj, pos):
        obj.pos = pos

    @classmethod
    def fix_vagueness(cls):
        for name, files in cls.objs.iteritems():
            if '?' not in files:
                continue
            if len(files) == 2:
                other_one = (set(files.keys()) - {'?'}).pop()
                files['?'] = files[other_one]
            # If all candidates are certainly outsiders (and not in mod_files)
            # we don't care what real symbols they are.
            elif any(file in config['mod_files'] for file in files):
                files['?'] = VagueSymbol(name)

    def __init__(self, name, file):
        self.name = name
        self.file = file
        self.pos = None

class VagueSymbol(Symbol):
    def assert_vague(self, old_method):
        def new_method(self, *args):
            name = super(VagueSymbol, self).__getattribute__('name')
            file = super(VagueSymbol, self).__getattribute__('file')
            if file in config['mod_files']:
                raise Exception('Unresolved vagueness in name: ' + name)
            return old_method(*args)
        return new_method

    def __init__(self, name):
        self.name = name
        self.__getattribute__ = self.assert_vague(self.__getattribute__)
        self.__hash__ = self.assert_vague(self.__hash__)

def read_config():
    with open('sched_boundary.yaml') as f:
        return load(f, Loader)

def all_meta_files():
    for r, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.sched_boundary'):
                yield os.path.join(r, file)

def read_meta(filename):
    with open(filename) as f:
        return json.load(f)

def find_init(meta):
    return [fn for fn in meta['fn'] if fn['init']]

def find_fn_ptr(meta):
    return [f
        for f in meta['fn_ptr']
        if Symbol.get(f) not in fn_symbol_classify['force_outsider']
        and Symbol.get(f) not in fn_symbol_classify['interface']
        and Symbol.get(f).file in config['mod_files']
    ]

def find_interface(meta):
    return [f
        for f in meta['interface']
        if Symbol.get(f) not in fn_symbol_classify['force_outsider']
        and Symbol.get(f).file in config['mod_files']
    ]

def find_force_outsider(meta):
    result = []
    for f in meta['fn']:
        if f['name'] in config['function']['force_outsider']:
            result.append(f)
    occurances = groupby(result, grouper=lambda function: Symbol.get(function).name,
                                 selector=lambda function: Symbol.get(function).file,
                                 reducer=lambda files: len(set(files)) > 1)
    for name, multioccur in occurances.iteritems():
        if multioccur:
            raise Exception('This force_outsider can mean multiple functions %s', name)
    return result

def find_initial_insider(meta):
    return [f
        for f in meta['fn']
        if  Symbol.get(f).file in config['mod_files']
        and Symbol.get(f) not in fn_symbol_classify['interface']
        and Symbol.get(f) not in fn_symbol_classify['fn_ptr']
        and Symbol.get(f) not in fn_symbol_classify['force_outsider']
    ]

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
# Disagrement: 4: vmlinux optimizes XXX to XXX.isra.1, plugsched remains XXX.

def find_in_vmlinux():
    in_vmlinux = set()
    for line in skipline(readelf('vmlinux', syms=True, wide=True, _iter=True), 3, None):
        fields = line.split()
        if len(fields) != 8: continue
        symtype, scope, key = fields[3], fields[4], fields[7]
        if symtype == 'FILE':
            filename = key
            # Disagreement 1:
            if filename in config['mod_files_basename']:
                filename = config['mod_files_basename'][filename]
            continue
        elif symtype != 'FUNC':
            continue

        # Disagreement 4
        if '.' in key:
            key = key[:key.index('.')]
        Symbol.inc_pos(key)
        if scope == 'LOCAL':
            if filename not in config['mod_files']: continue
            sym = Symbol.get({'name': key, 'file': filename}, no_create=True)
            if not sym: # Disagreement 2
                count, sym = Symbol.get_in_any({'name': key}, files=config['mod_header_files'])
                if count == 0: continue
        else:
            # Disagreement 3
            count, sym = Symbol.get_in_any({'name': key}, files=config['mod_files'])
            if count == 0: continue

        assert sym
        in_vmlinux.add(sym)
        if scope == 'LOCAL':
            Symbol.assign_cur_pos(sym)
        else:
            Symbol.assign_pos(sym, 0)

    return in_vmlinux

# __insiders is a global variable only used by these two functions
__insiders = None

def inflect_one(edge):
    to_sym = Symbol.get(edge['to'])
    if to_sym in __insiders:
        sym = Symbol.get(edge['from'])
        if sym not in __insiders and \
           sym not in fn_symbol_classify['interface'] and \
           sym not in fn_symbol_classify['fn_ptr'] and \
           sym not in fn_symbol_classify['init']:
            return to_sym
    return None

def inflect(initial_insiders, edges):
    global __insiders
    __insiders = set(initial_insiders)
    while True:
        delete_insider = filter(None, map(inflect_one, edges))
        if not delete_insider:
            break
        __insiders -= set(delete_insider)
    return __insiders

def groupby(it, grouper, selector, reducer):
    sorted_list = sorted(it, key=grouper)
    return dict((k, reducer(map(selector, v))) for k, v in _groupby(sorted_list, grouper))

if __name__ == '__main__':
    # Read all files generated by SchedBoundaryCollect, and export_jump.h, and sched_boundary.yaml
    config = read_config()
    config['mod_files_basename'] = {os.path.basename(f): f for f in config['mod_files']}
    config['mod_header_files'] = [f for f in config['mod_files'] if f.endswith('.h')]
    metas = map(read_meta, all_meta_files())

    # Create symbol objs
    for meta in metas:
        for fn in meta['fn']:
            Symbol.get(fn)
        for fn in meta['fn_ptr']:
            Symbol.get(fn)
        for edge in meta['edge']:
            Symbol.get(edge['from'])
            Symbol.get(edge['to'])
        # TODO struct should do this too
    Symbol.fix_vagueness()

    # Init all kinds of functions
    for process in ['init', 'force_outsider', 'interface', 'fn_ptr', 'initial_insider']:
        processor = globals()['find_' + process]
        fn_symbol_classify[process] = {Symbol.get(fn) for fn in chain(map(processor, metas))}
    fn_symbol_classify['in_vmlinux'] = find_in_vmlinux()

    # Init edges
    edges = list(chain(m['edge'] for m in metas))

    # Inflect outsider functions
    fn_symbol_classify['insider'] = inflect(fn_symbol_classify['initial_insider'], edges)
    fn_symbol_classify['outsider'] = (fn_symbol_classify['initial_insider'] - fn_symbol_classify['insider']) | fn_symbol_classify['force_outsider']
    fn_symbol_classify['optimized_out'] = fn_symbol_classify['outsider'] - fn_symbol_classify['in_vmlinux']
    fn_symbol_classify['tainted'] = (fn_symbol_classify['interface'] | fn_symbol_classify['fn_ptr'] | fn_symbol_classify['insider']) & fn_symbol_classify['in_vmlinux']

    # TODO Better output the file too to avoid duplicy ???
    for output_item in ['outsider', 'fn_ptr', 'interface', 'init', 'insider', 'optimized_out']:
        config['function'][output_item] = [fn.name for fn in fn_symbol_classify[output_item]]

    # Handle Struct public fields. The right hand side gives an example
    struct_properties = {
        struct: {                                                                          # cfs_rq:
            'public_fields': set(chain(                                                         #   public_fields:
                [field for field, users in m['struct'][struct]['public_fields'].iteritems()     #   - nr_uninterruptible
                       if any(user['file'] not in config['mod_files'] for user in users)        #   # ca_uninterruptible (in cpuacct.c) referenced it.
                       or set(map(Symbol.get, users)) & fn_symbol_classify['outsider']]         #   # maybe some outsider (in scheduler c files) referenced it.
                for m in metas                                                                  ## for all files output by SchedBoundaryCollect
                if struct in m['struct']                                                        ## and only if this file has structure information
             )),
            'all_fields': set(chain(
                m['struct'][struct]['all_fields']
                for m in metas
                if struct in m['struct']
            ))
        }
        for struct in set(chain(m['struct'].keys() for m in metas))
    }

    with open('sched_boundary_doc.yaml', 'w') as f:
        dump(struct_properties, f, Dumper)
    with open('sched_boundary_extract.yaml', 'w') as f:
        dump(config, f, Dumper)
    with open('tainted_functions', 'w') as f:
        f.write('\n'.join(["{fn} {sympos}".format(fn=fn.name, sympos=fn.pos) for fn in fn_symbol_classify['tainted']]))
    with open('interface_fn_ptrs', 'w') as f:
        f.write('\n'.join([fn for fn in config['function']['interface']]))
        f.write('\n'.join(['__mod_' + fn for fn in config['function']['fn_ptr']]))
