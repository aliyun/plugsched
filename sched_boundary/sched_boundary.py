# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from collections import defaultdict
from builtins import super
from itertools import izip, groupby as _groupby
from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
import json
import re
import os

# Use set as the default sequencer for yaml
Loader.add_constructor(resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
                     lambda loader, node: set(loader.construct_sequence(node)))
Dumper.add_representer(set, lambda dumper, node: dumper.represent_list(node))

# tmp directory to store middle files
tmpdir = None

# directory to store schedule module source code
modpath = None

class SchedBoundary(object):
    def __init__(self, config):
        with open(config) as f:
            self.config = load(f, Loader)
        self.mod_files = self.config['mod_files']
        self.mod_srcs = {f for f in self.mod_files if f.endswith('.c')}
        self.mod_hdrs = self.mod_files - self.mod_srcs

    def process_passes(self, p, _):
        if p.name != '*free_lang_data':
            return
        self.final_work()

    def register_cbs(self):
        if hasattr(self, 'function_define'):
            gcc.register_callback(gcc.PLUGIN_FINISH_PARSE_FUNCTION, self.function_define)
        if hasattr(self, 'var_declare'):
            gcc.register_callback(gcc.PLUGIN_FINISH_DECL, self.var_declare)
        if hasattr(self, 'final_work'):
            gcc.register_callback(gcc.PLUGIN_PASS_EXECUTION, self.process_passes)
        if hasattr(self, 'include_file'):
            gcc.register_callback(gcc.PLUGIN_INCLUDE_FILE, self.include_file)

# Workarounds
class GccBugs(object):
    array_pointer_re = re.compile(r'(.*)\[([0-9]*)\] \*\s*([^,\);]*)')

    # struct cpumask[1] * doms_cur -> struct cpumask (*doms_cur)[1]
    @staticmethod
    def array_pointer(decl, str):
        return GccBugs.array_pointer_re.sub(r'\1 (*\3)[\2]', str)

    @staticmethod
    def enum_type_name(decl, str):
        if isinstance(decl.type, gcc.EnumeralType):
            i = str.find(decl.type.name.name)
            return str[:i] + 'enum ' + str[i:]
        else:
            return str

    # extern type array[<unknown>] -> extern type array[]
    @staticmethod
    def array_size(decl, str):
        return str.replace('[<unknown>]', '[]')

    @staticmethod
    def fix(decl, str):
        for bugfix in [GccBugs.array_pointer, GccBugs.enum_type_name,
                       GccBugs.array_size]:
            str = bugfix(decl, str)
        return str

class SchedBoundaryExtract(SchedBoundary):
    def __init__(self):
        super().__init__(tmpdir + 'sched_boundary_extract.yaml')
        self.fn_dict = {}
        self.fn_ptr_list = []
        self.interface_list = []
        self.var_list = []
        self.fake = 'fake.c'

    def register_cbs(self):
        if gcc.get_main_input_filename() in self.mod_srcs | {self.fake}:
            super().register_cbs()

    def function_define(self, decl, _):
        # only func definition will trigger PLUGIN_FINISH_PARSE_FUNCTION
        loc = gcc.get_location()
        src_file = gcc.get_main_input_filename()

        # filter out *.h in *.c but excluding fake.c
        if src_file != self.fake and loc.file != src_file:
            return
        # filter out *.h in fake.c but excluding mod headers
        if src_file == self.fake and loc.file not in self.mod_hdrs:
            return

        self.fn_dict.setdefault(loc.file, list())
        assert(isinstance(decl, gcc.FunctionDecl))

        obj = (decl.name, loc.file)
        interface_export_fmt = "EXPORT_PLUGSCHED({fn}, {ret}, {params})\n"
        fn_ptr_export_fmt = "PLUGSCHED_FN_PTR({fn}, {ret}, {params})\n"
        # translate index to start with 0
        if obj in self.config['function']['sched_outsider'] or \
           obj in self.config['function']['init']:
            self.fn_dict[loc.file].append([decl,
                decl.function.start.line - 1,
                decl.function.start.column - 1,
                decl.function.end.line - 1,
                decl.function.end.column - 1])
        elif obj in self.config['function']['fn_ptr']:
            fn_ptr_export = fn_ptr_export_fmt.format(
                fn=decl.name,
                ret=GccBugs.fix(decl.result, decl.result.type.str_no_uid),
                params=", ".join(GccBugs.fix(arg, arg.type.str_no_uid) \
                        for arg in decl.arguments) if decl.arguments else "void"
            )
            self.fn_ptr_list.append([decl,
                fn_ptr_export,
                decl.location.line - 1,
                decl.location.column - 1,
                decl.function.end.line - 1,
                decl.function.end.column - 1])
        elif obj in self.config['function']['interface']:
            interface_export = interface_export_fmt.format(
                fn=decl.name,
                ret=GccBugs.fix(decl.result, decl.result.type.str_no_uid),
                params=", ".join(GccBugs.fix(arg, arg.type.str_no_uid) \
                        for arg in decl.arguments) if decl.arguments else "void"
            )
            self.interface_list.append([decl,
                interface_export,
                decl.location.line - 1,
                decl.location.column - 1,
                decl.function.end.line - 1,
                decl.function.end.column - 1])

    def var_declare(self, decl, _):
        def anonymous_type_var(var_decl):
            t = var_decl.type
            while isinstance(t, (gcc.PointerType, gcc.ArrayType)):
                t = t.type
            return t.name is None

        loc = gcc.get_location()

        # filter out *.h
        if loc.file != gcc.get_main_input_filename():
            return

        if not isinstance(decl, gcc.VarDecl):
            return

        if decl.name in self.config['global_var']['force_private']:
            return

        # share public (non-static) variables by default
        if decl.public or decl.name in self.config['global_var']['extra_public']:
            if anonymous_type_var(decl):
                self.var_list.append((decl,
                    decl.type.stub.location.line - 1,
                    loc.line - 1))
            else:
                self.var_list.append((decl,
                    decl.location.line - 1,
                    loc.line - 1))

    def final_work(self):
        if gcc.get_main_input_filename() == self.fake:
            # no function definition in sched-pelt.h
            for header in self.mod_hdrs:
                self.fn_dict.setdefault(header, list())

        for src_f in self.fn_dict.keys():
            self.extract_file(src_f)

    def extract_file(self, src_f):
        with open(src_f) as in_f, open(src_f + '.export_jump.h', 'w') as fn_export_jump:
            lines = in_f.readlines()

            for decl, fn_row_start, fn_col_start, fn_row_end, __ in self.fn_dict[src_f]:
                if 'always_inline' in decl.attributes or decl.inline is True or \
                        (decl.name, src_f) in self.config['function']['optimized_out']:
                    lines[fn_row_end] += "/* DON'T MODIFY FUNCTION {}, IT'S NOT PART OF SCHEDMOD */\n".format(decl.name)
                else:
                    # convert function body "{}" to ";"
                    # only handle normal kernel function definition
                    lines[fn_row_start] = lines[fn_row_start][: fn_col_start] + ";\n"
                    for i in range(fn_row_start+1, fn_row_end+1):
                        lines[i] = ''

            for decl, export, fn_row_start, fn_col_start, fn_row_end, fn_col_end in self.fn_ptr_list:
                fn_export_jump.write(export)
                decl.public = True
                decl.external = True
                decl.static = False
                new_name = '__mod_' + decl.name
                lines[fn_row_start] = lines[fn_row_start][:fn_col_start] + \
                    lines[fn_row_start][fn_col_start:].replace(decl.name, new_name)
                lines[fn_row_end] = lines[fn_row_end] + '\n' + \
                    "/* DON'T MODIFY SIGNATURE OF FUNCTION {}, IT'S CALLBACK FUNCTION */\n".format(new_name) + \
                    GccBugs.enum_type_name(decl.result, decl.str_decl) + '\n'

            for decl, export, fn_row_start, fn_col_start, fn_row_end, fn_col_end in self.interface_list:
                fn_export_jump.write(export)

                # everyone know that syscall ABI should be consistent
                if any(decl.name.startswith(prefix) for prefix in self.config['interface_prefix']):
                    continue
                lines[fn_row_end] += \
                    "/* DON'T MODIFY SIGNATURE OF FUNCTION {}, IT'S INTERFACE FUNCTION */\n".format(decl.name)

            for decl, row_start, row_end in self.var_list:
                decl.static = False
                decl.external = True
                decl.public = True
                decl.initial = 0

                line = lines[row_start]
                if 'DEFINE_PER_CPU(' in line:
                    line = line.replace('DEFINE_PER_CPU(', 'DECLARE_PER_CPU(').replace('static ', '')
                elif 'DEFINE_PER_CPU_SHARED_ALIGNED(' in line:
                    line = line.replace('DEFINE_PER_CPU_SHARED_ALIGNED(', 'DECLARE_PER_CPU_SHARED_ALIGNED(').replace('static ', '')
                elif 'DEFINE_STATIC_KEY_FALSE(' in line:
                    line = line.replace('DEFINE_STATIC_KEY_FALSE(', 'DECLARE_STATIC_KEY_FALSE(').replace('static ', '')
                elif 'DEFINE_STATIC_KEY_TRUE(' in line:
                    line = line.replace('DEFINE_STATIC_KEY_TRUE(', 'DECLARE_STATIC_KEY_TRUE(').replace('static ', '')
                else:
                    line = GccBugs.fix(decl, decl.str_decl) + '\n'
                lines[row_start] = line
                for i in range(row_start+1, row_end+1):
                    lines[i] = ''

            with open(modpath + os.path.basename(src_f), 'w') as out_f:
                out_f.writelines(lines)

class SchedBoundaryCollect(SchedBoundary):
    def __init__(self):
        super().__init__(tmpdir + 'sched_boundary.yaml')
        self.fn_properties = []
        self.var_properties = []
        self.edge_properties = []
        self.struct_properties = {}
        self.fn_ptr_properties = []
        self.interface_properties = []
        self.seek_public_field = False

    def decl_in_section(self, decl, section):
        for name, val in decl.attributes.items():
            # Canonicalized name "section" since gcc-8.1.0, and
            # Uncanonicalized legacy name "__section__" before 8.1.0
            if name in ('section', '__section__'):
                assert len(val) == 1
                return val[0].constant == section
        return False

    def include_file(self, header, _):
        if header in self.mod_hdrs:
            self.seek_public_field = True

    def collect_fn(self):
        src_f = gcc.get_main_input_filename()

        for node in gcc.get_callgraph_nodes():
            decl = node.decl
            if not isinstance(decl.context, gcc.TranslationUnitDecl):
                continue
            # Ignore alias function for now ??
            if decl.function is None:
                continue
            properties = {
                "name": decl.name,
                "init": self.decl_in_section(decl, '.init.text'),
                "file": os.path.relpath(decl.location.file),
                "l_brace_loc": (decl.function.start.line, decl.function.start.column),
                "r_brace_loc": (decl.function.end.line, decl.function.end.column),
                "fn_name_loc": (decl.location.line, decl.location.column),
                "external": decl.external,
                "public": decl.public,
                "static": decl.static,
                "signature": (decl.name, os.path.relpath(decl.location.file)),
            }
            self.fn_properties.append(properties)

            # interface candidates must belongs to module source files
            if not src_f in self.mod_srcs:
                continue

            if decl.name in self.config['function']['interface'] or \
               any(decl.name.startswith(prefix) for prefix in self.config['interface_prefix']):
                self.interface_properties.append([
                    decl.name,
                    os.path.relpath(decl.location.file),
                ])

    def var_declare(self, decl, _):
        def var_decl_start_loc(decl):
            base_type = decl.type
            while isinstance(base_type, (gcc.PointerType, gcc.ArrayType)):
                base_type = base_type.type
            # It's a bug of gcc-python-plugin doesn't have main_variant for FunctionType
            if hasattr(base_type, 'main_variant'):
                base_type = base_type.main_variant
            if base_type.name is None and isinstance(base_type, (gcc.EnumeralType, gcc.RecordType)):
                return base_type.stub.location
            return decl.location

        if not isinstance(decl, gcc.VarDecl):
            return
        if isinstance(decl.context, gcc.FunctionDecl):
            return
        loc = gcc.get_location()
        if loc.file != gcc.get_main_input_filename():
            return
        properties = {
            "name": decl.name,
            "file": os.path.relpath(decl.location.file),
            "var_name_loc": (decl.location.line, decl.location.column),
            "dec_start_loc_line": var_decl_start_loc(decl).line,
            "dec_end_loc_line": loc.line,
            "external": decl.external,
            "public": decl.public,
            "static": decl.static,
        }
        self.var_properties.append(properties)

    def collect_fn_ptrs(self):
        # return True means we stop walk subtree
        def mark_fn_ptr(op, caller):
            if isinstance(op, gcc.FunctionDecl) and not self.decl_in_section(op, '.init.text'):
                self.fn_ptr_properties.append(
                    [op.name, os.path.relpath(op.location.file) if op.function else '?']
                )

        # Find fn ptrs in function body
        for node in gcc.get_callgraph_nodes():
            # Ignore alias, it's insignificant at all
            if node.decl.function is None:
                continue
            for stmt in self.each_stmt(node):
                if isinstance(stmt, gcc.GimpleCall):
                    # Ignore direct calls
                    for rhs in stmt.rhs[1:]:
                        if rhs: rhs.walk_tree(mark_fn_ptr, node.decl)
                else:
                    stmt.walk_tree(mark_fn_ptr, node.decl)

        # Find fn ptrs in variable init value
        for var in gcc.get_variables():
            if var.decl.initial and not self.decl_in_section(var.decl, '.discard.addressable'):
                var.decl.initial.walk_tree(mark_fn_ptr, var.decl)

    def collect_struct(self):
        public_fields = defaultdict(set)

        def mark_public_field(op, node):
            if isinstance(op, gcc.ComponentRef):
                loc_file = os.path.relpath(op.field.context.stub.location.file)
                if loc_file in self.mod_hdrs and op.field.context.name is not None:
                    public_fields[op.field.context].add((node.decl, op.field))

        for node in gcc.get_callgraph_nodes():
            # Ignore alias, it's insignificant at all
            if node.decl.function is None:
                continue
            for stmt in self.each_stmt(node):
                stmt.walk_tree(mark_public_field, node)

        def groupby(it, grouper, selector):
            sorted_list = sorted(it, key=grouper)
            return dict((k, map(selector, v)) for k, v in _groupby(sorted_list, grouper))

        for struct, user_fields in public_fields.iteritems():
            self.struct_properties[struct.name.name] = {
                "all_fields": [f.name for f in struct.fields if f.name],
                "public_fields": groupby(user_fields,
                    grouper=lambda (user, field): field.name,
                    selector=lambda (user, field): (user.name, os.path.relpath(user.location.file)))
            }

    def collect_edges(self):
        for node in gcc.get_callgraph_nodes():
            if self.decl_in_section(node.decl, '.init.text'):
                continue

            # alias function
            if node.decl.function is None:
                real_name = node.decl.attributes['alias'][0].str_no_uid.replace('"','')
                properties = {
                    "from": (node.decl.name, os.path.relpath(node.decl.location.file)),
                    "to": (real_name, "?"),
                }
                self.edge_properties.append(properties)
                continue

            for stmt in self.each_call_stmt(node):
                if not stmt.fndecl:
                    continue
                assert node.decl.function
                properties = {
                    "from": (
                        node.decl.name,
                        os.path.relpath(node.decl.location.file),
                    ),
                    "to": (
                        stmt.fndecl.name,
                        os.path.relpath(stmt.fndecl.location.file) if stmt.fndecl.function else '?',
                    ),
                }
                self.edge_properties.append(properties)

    def each_stmt(self, node):
        for bb in node.decl.function.cfg.basic_blocks:
            if bb.gimple:
                for stmt in bb.gimple:
                    yield stmt

    def each_call_stmt(self, node):
        for bb in node.decl.function.cfg.basic_blocks:
            if not bb.gimple:
                continue
            stmts = list(bb.gimple)
            for i, stmt in enumerate(stmts):
                if isinstance(stmt, gcc.GimpleCall):
                    yield stmt

    def final_work(self):
        self.collect_edges()
        self.collect_fn()
        self.collect_fn_ptrs()
        self.collect_struct()

        collect = {
            "fn": self.fn_properties,
            "var": self.var_properties,
            "edge": self.edge_properties,
            "fn_ptr": self.fn_ptr_properties,
            "interface": self.interface_properties,
            "struct": self.struct_properties
        }
        with open(gcc.get_main_input_filename() + '.sched_boundary', 'w') as f:
            json.dump(collect, f, indent=4)

if __name__ == '__main__':
    import gcc

    stage = gcc.argument_dict['stage']
    tmpdir = gcc.argument_dict['tmpdir']
    modpath = gcc.argument_dict['modpath']

    if stage == 'extract':
        sched_boundary = SchedBoundaryExtract()
    elif stage == "collect":
        sched_boundary = SchedBoundaryCollect()
    else:
        raise Exception("")

    sched_boundary.register_cbs()
