#!/usr/bin/env python3
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from collections import defaultdict
from itertools import groupby as _groupby
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

class GccBugs(object):
    array_pointer_re = re.compile(r'(.*)\[([0-9]*)\] \*\s*([^,\);]*)')

    # struct cpumask[1] * doms_cur -> struct cpumask (*doms_cur)[1]
    @staticmethod
    def array_pointer(decl, str):
        return GccBugs.array_pointer_re.sub(r'\1 (*\3)[\2]', str)

    @staticmethod
    def typedef(decl, str):
        t = decl.type

        while isinstance(t, (gcc.PointerType, gcc.ArrayType)):
            t = t.dereference

        if isinstance(t.name, gcc.TypeDecl):
            name = t.name.name
            return str.replace('struct ' + name, name)
        else:
            return str

    @staticmethod
    def enum_type_name(decl, str):
        if isinstance(decl.type, gcc.EnumeralType):
            i = str.find(decl.type.name.name)
            return str[:i] + 'enum ' + str[i:]
        else:
            return str

    @staticmethod
    def is_val_list(arg):
        return isinstance(arg.type, gcc.PointerType) and \
               isinstance(arg.type.dereference, gcc.RecordType) and \
               isinstance(arg.type.dereference.name, gcc.Declaration) and \
               arg.type.dereference.name.is_builtin and \
               arg.type.dereference.name.name == '__va_list_tag'

    @staticmethod
    def va_list(decl, str):
        if GccBugs.is_val_list(decl):
            return str.replace("struct  *", "va_list")
        return str

    # extern type array[<unknown>] -> extern type array[]
    @staticmethod
    def array_size(decl, str):
        return str.replace('[<unknown>]', '[]')

    @staticmethod
    def fix(decl, str):
        for bugfix in [GccBugs.array_pointer, GccBugs.enum_type_name,
                       GccBugs.array_size, GccBugs.typedef, GccBugs.va_list]:
            str = bugfix(decl, str)
        return str

    @staticmethod
    def variadic_function(decl, signature):
        if decl.str_decl.find("...") >= 0:
            signature["params"] += ", ..."


class Collection(object):
    def __init__(self):
        with open(tmpdir + 'boundary.yaml') as f:
            self.config = load(f, Loader)
        self.mod_files = self.config['mod_files']
        self.mod_hdrs  = [f for f in self.mod_files if f.endswith('.h')]
        self.mod_srcs  = [f for f in self.mod_files if f.endswith('.c')]
        self.sdcr      = [] if self.config['sidecar'] is None else self.config['sidecar']
        self.sdcr_srcs = [f[1] for f in self.sdcr]
        self.fn_properties = []
        self.var_properties = []
        self.edge_properties = []
        self.struct_properties = {}
        self.callback_properties = []
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
                "l_brace_loc": (decl.function.start.line - 1, decl.function.start.column - 1),
                "r_brace_loc": (decl.function.end.line - 1, decl.function.end.column - 1),
                "name_loc": (decl.location.line - 1, decl.location.column - 1),
                "external": decl.external,
                "public": decl.public,
                "static": decl.static,
                "inline": decl.inline or 'always_inline' in decl.attributes,
                "signature": (decl.name, os.path.relpath(decl.location.file)),
                "decl_str": None,
            }
            self.fn_properties.append(properties)

            # interface candidates must belongs to module source files
            if not src_f in self.mod_srcs + self.sdcr_srcs:
                continue

            decl_str = {
                "fn": decl.name,
                "ret": GccBugs.fix(decl.result, decl.result.type.str_no_uid),
                "params": ', '.join(GccBugs.fix(arg, arg.type.str_no_uid) \
                        for arg in decl.arguments) if decl.arguments else 'void'
            }

            GccBugs.variadic_function(decl, decl_str)
            properties['decl_str'] = decl_str

            if decl.name in self.config['function']['interface'] or \
               any(decl.name.startswith(prefix) for prefix in self.config['interface_prefix']):
                self.interface_properties.append([
                    decl.name,
                    os.path.relpath(decl.location.file),
                ])

    def collect_var(self):
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

        for var in gcc.get_variables():
            decl = var.decl
            if not decl.location:
                continue

            properties = {
                "name": decl.name,
                "file": os.path.relpath(decl.location.file),
                "name_loc": (decl.location.line - 1, decl.location.column - 1),
                "decl_start_line": var_decl_start_loc(decl).line - 1,
                "external": decl.external,
                "public": decl.public,
                "static": decl.static,
                "decl_str": None,
            }

            # tricky skill to get right str_decl
            if decl.location.file in self.mod_srcs + self.sdcr_srcs:
                decl_str = decl.str_decl.split('=')[0].strip(' ;') + ';'
                decl_str = decl_str.replace('static ', 'extern ')
                properties['decl_str'] = GccBugs.fix(decl, decl_str)

            self.var_properties.append(properties)

    def collect_callbacks(self):
        # return True means we stop walk subtree
        def mark_callback(op, caller):
            if isinstance(op, gcc.FunctionDecl) and not self.decl_in_section(op, '.init.text'):
                self.callback_properties.append(
                    [op.name, os.path.relpath(op.location.file) if op.function else '?']
                )

        # Find callbacks in function body
        for node in gcc.get_callgraph_nodes():
            # Ignore alias, it's insignificant at all
            if node.decl.function is None:
                continue
            for stmt in self.each_stmt(node):
                if isinstance(stmt, gcc.GimpleCall):
                    # Ignore direct calls
                    for rhs in stmt.rhs[1:]:
                        if rhs: rhs.walk_tree(mark_callback, node.decl)
                else:
                    stmt.walk_tree(mark_callback, node.decl)

        # Find callbacks in variable init value
        for var in gcc.get_variables():
            decl = var.decl
            type_name = '' if not decl.type.name else decl.type.name.name

            # struct sched_class is purely private
            if decl.initial and type_name != 'sched_class' and \
                    not self.decl_in_section(decl, '.discard.addressable'):
                decl.initial.walk_tree(mark_callback, decl)

    def collect_struct(self):
        public_fields = defaultdict(set)

        def mark_public_field(op, node, parent_component_ref):
            if isinstance(op, gcc.ComponentRef):
                if isinstance(op.target, gcc.ComponentRef):
                    parent_component_ref[op.target] = op

                context = op.field.context
                while op.field.name is None and op in parent_component_ref:
                    op = parent_component_ref[op]
                field = op.field

                loc_file = os.path.relpath(context.stub.location.file)
                if loc_file in self.mod_hdrs and context.name is not None:
                    public_fields[context].add((node.decl, field))

        for node in gcc.get_callgraph_nodes():
            # Ignore alias, it's insignificant at all
            if node.decl.function is None:
                continue
            for stmt in self.each_stmt(node):
                stmt.walk_tree(mark_public_field, node, {})

        def groupby(it, grouper, selector):
            sorted_list = sorted(it, key=grouper)
            return dict((k, list(map(selector, v))) for k, v in _groupby(sorted_list, grouper))

        for struct, user_fields in public_fields.items():
            self.struct_properties[struct.name.name] = {
                "all_fields": [f.name for f in struct.fields if f.name],
                "public_fields": groupby(user_fields,
                    grouper=lambda user_and_field: user_and_field[1].name,
                    selector=lambda user_and_field: (user_and_field[0].name, os.path.relpath(user_and_field[0].location.file)))
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

    def process_passes(self, p, _):
        if p.name != '*free_lang_data':
            return

        self.collect_edges()
        self.collect_fn()
        self.collect_callbacks()
        self.collect_struct()
        self.collect_var()

        collect = {
            "fn": self.fn_properties,
            "var": self.var_properties,
            "edge": self.edge_properties,
            "callback": self.callback_properties,
            "interface": self.interface_properties,
            "struct": self.struct_properties
        }
        with open(gcc.get_main_input_filename() + '.boundary', 'w') as f:
            json.dump(collect, f, indent=4)

    def register_cbs(self):
        gcc.register_callback(gcc.PLUGIN_PASS_EXECUTION, self.process_passes)


if __name__ == '__main__':
    import gcc

    tmpdir = gcc.argument_dict['tmpdir']
    modpath = gcc.argument_dict['modpath']

    collect = Collection()
    collect.register_cbs()
