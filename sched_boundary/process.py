from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
from itertools import izip, groupby as _groupby
from itertools import chain as _chain
from collections import defaultdict
import logging
import json
import sys
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

    @classmethod
    def get(cls, desc):
        name = desc['name']
        file = desc['file']
        if name in cls.objs and file in cls.objs[name]:
            return cls.objs[name][file]
        new_obj = cls(name, file)
        cls.objs.setdefault(name, dict())[file] = new_obj
        return new_obj

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

def input_interface():
    names = []
    with open('kernel/sched/mod/export_jump.h') as f:
        for line in f.readlines():
            if 'EXPORT_PLUGSCHED' not in line:
                continue
            names.append(line[len('EXPORT_PLUGSCHED')+1:line.index(',')])
    return names

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

__input_interface_names = None
def find_interface(meta):
    return [f
        for f in meta['fn']
        if (f['syscall'] or f['name'] in __input_interface_names)
        and Symbol.get(f) not in fn_symbol_classify['force_outsider']
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
    __input_interface_names = input_interface()
    for process in ['init', 'force_outsider', 'interface', 'fn_ptr', 'initial_insider']:
        processor = globals()['find_' + process]
        fn_symbol_classify[process] = [Symbol.get(fn) for fn in chain(map(processor, metas))]
    del __input_interface_names

    # Init edges
    edges = list(chain(m['edge'] for m in metas))

    # Inflect outsider functions
    fn_symbol_classify['insider'] = inflect(fn_symbol_classify['initial_insider'], edges)
    fn_symbol_classify['outsider'] = (set(fn_symbol_classify['initial_insider']) - set(fn_symbol_classify['insider'])) | set(fn_symbol_classify['force_outsider'])

    # TODO Better output the file too to avoid duplicy ???
    for output_item in ['outsider', 'fn_ptr', 'interface', 'init', 'insider']:
        config['function'][output_item] = [fn.name for fn in fn_symbol_classify[output_item]]

    # Find optimized out functions
    with open(sys.argv[1]) as f:
        config['function']['optimized_out'] = set(config['function']['outsider']) - set([l.split()[2] for l in f.readlines()])

    # Handle Struct public fields. The right hand side gives an example
    struct_properties = {
        struct: {                                                                          # cfs_rq:
            'public_fields': set(chain(                                                         #   public_fields:
                [field for field, users in m['struct'][struct]['public_fields'].iteritems()     #   - nr_uninterruptible
                       if any(user['file'] not in config['mod_files'] for user in users)        #   # ca_uninterruptible (in cpuacct.c) referenced it.
                       or set(map(Symbol.get, users)) & set(fn_symbol_classify['outsider'])]    #   # maybe some outsider (in scheduler c files) referenced it.
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

    # TODO Better just integrate sleepable.py into this.
    edges = list(chain([(e['from']['name'], e['to']['name'], str(e['carry']).replace(' ', ''), e['tail']) for e in m['edge']] for m in metas))

    with open('sched_boundary_doc.yaml', 'w') as f:
        dump(struct_properties, f, Dumper)
    with open('sched_boundary_extract.yaml', 'w') as f:
        dump(config, f, Dumper)
    with open('tainted_functions', 'w') as f:
        func = config['function']
        f.write('\n'.join(func['interface'] + func['fn_ptr'] + func['insider']))
