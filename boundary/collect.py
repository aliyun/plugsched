#!/usr/bin/env python3
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
"""Use GCC Python Plugin to collect source code information"""

import re
import os
import json
from collections import defaultdict
from itertools import groupby as _groupby
from yaml import load, resolver, CLoader as Loader

# Use set as the default sequencer for yaml
Loader.add_constructor(
    resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
    lambda loader, node: set(loader.construct_sequence(node)))


class GccBugs(object):
    array_ptr_re = re.compile(r'(.*)\[([0-9]*)\] \*\s*([^,\);]*)')

    @staticmethod
    def array_pointer(decl, str):
        """struct cpumask[1] *doms_cur -> struct cpumask (*doms_cur)[1]"""
        return GccBugs.array_ptr_re.sub(r'\1 (*\3)[\2]', str)

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
        return (isinstance(arg.type, gcc.PointerType)
                and isinstance(arg.type.dereference, gcc.RecordType)
                and isinstance(arg.type.dereference.name, gcc.Declaration)
                and arg.type.dereference.name.is_builtin
                and arg.type.dereference.name.name == '__va_list_tag')

    @staticmethod
    def va_list(decl, str):
        if GccBugs.is_val_list(decl):
            return str.replace('struct  *', 'va_list')
        return str

    @staticmethod
    def array_size(decl, str):
        """extern type array[<unknown>] -> extern type array[]"""
        return str.replace('[<unknown>]', '[]')

    @staticmethod
    def fix(decl, str):
        for bugfix in [
                GccBugs.array_pointer, GccBugs.enum_type_name,
                GccBugs.array_size, GccBugs.typedef, GccBugs.va_list
        ]:
            str = bugfix(decl, str)
        return str

    @staticmethod
    def variadic_function(decl, signature):
        if decl.str_decl.find('...') >= 0:
            signature['params'] += ', ...'

    @staticmethod
    def var_decl_start_loc(decl):
        base_type = decl.type
        while isinstance(base_type, (gcc.PointerType, gcc.ArrayType)):
            base_type = base_type.type
        if base_type.name is None and isinstance(
                base_type, (gcc.EnumeralType, gcc.RecordType)):
            return base_type.main_variant.stub.location
        return decl.location


class Collection(object):

    def __init__(self, tmp_dir):
        with open(tmp_dir + 'boundary.yaml') as f:
            self.config = load(f, Loader)

        self.fn_prop = []
        self.cb_prop = []
        self.var_prop = []
        self.intf_prop = []
        self.edge_prop = []
        self.struct_prop = {}
        self.mod_files = self.config['mod_files']
        self.mod_hdrs = [f for f in self.mod_files if f.endswith('.h')]
        self.mod_srcs = [f for f in self.mod_files if f.endswith('.c')]
        self.sdcr = self.config['sidecar'] or set()
        self.sdcr_srcs = [f[1] for f in self.sdcr]

    def relpath(self, decl):
        """Get relative path from declaration object"""
        return os.path.relpath(decl.location.file)

    def decl_sig(self, decl):
        """Get function signature from declaration object"""
        if decl.function is None:
            return (decl.name, '?')
        return (decl.name, os.path.relpath(decl.location.file))

    def decl_in_section(self, decl, section):
        """Whether declaration is in a specific text section"""
        for name, val in decl.attributes.items():
            """Canonicalized name "section" since gcc-8.1.0, and
            uncanonicalized legacy name "__section__" before 8.1.0
            """
            if name in ('section', '__section__'):
                assert len(val) == 1
                return val[0].constant == section
        return False

    def decl_is_weak(self, decl):
        """Whether declaration is weak"""
        return '__weak__' in decl.attributes or 'weak' in decl.attributes

    def collect_fn(self):
        """Collect all funtion properties, including interface functions"""
        src_f = gcc.get_main_input_filename()

        for node in gcc.get_callgraph_nodes():
            decl = node.decl
            if not isinstance(decl.context, gcc.TranslationUnitDecl):
                continue
            # Ignore alias function for now ??
            if decl.function is None:
                continue

            l_loc = decl.function.start
            r_loc = decl.function.end
            name_loc = decl.location

            properties = {
                'name': decl.name,
                'init': self.decl_in_section(decl, '.init.text'),
                'file': self.relpath(decl),
                'l_brace_loc': (l_loc.line - 1, l_loc.column - 1),
                'r_brace_loc': (r_loc.line - 1, r_loc.column - 1),
                'name_loc': (name_loc.line - 1, name_loc.column - 1),
                'external': decl.external,
                'public': decl.public,
                'static': decl.static,
                'inline': decl.inline or 'always_inline' in decl.attributes,
                'weak': self.decl_is_weak(decl),
                'signature': self.decl_sig(decl),
                'decl_str': None,
            }
            self.fn_prop.append(properties)

            # interface candidates must belongs to module source files
            if src_f in self.mod_srcs + self.sdcr_srcs:
                decl_str = {
                    'fn': decl.name,
                    'ret': GccBugs.fix(decl.result, decl.result.type.str_no_uid),
                    'params': ', '.join(GccBugs.fix(arg, arg.type.str_no_uid) \
                            for arg in decl.arguments) if decl.arguments else 'void'
                }

                GccBugs.variadic_function(decl, decl_str)
                properties['decl_str'] = decl_str

                interface = self.config['function']['interface']
                syscall = self.config['interface_prefix']

                # sidecars shouln't treat syscall funtions as interfaces
                if src_f in self.mod_srcs and (
                    decl.name in interface or any(
                        decl.name.startswith(prefix) for prefix in syscall
                    )
                ):
                    self.intf_prop.append(list(self.decl_sig(decl)))

    def collect_var(self):
        """Collect properties of all global variables"""
        for var in gcc.get_variables():
            decl = var.decl
            if not decl.location:
                continue
            if not isinstance(decl.context, gcc.TranslationUnitDecl):
                continue

            properties = {
                'name': decl.name,
                'file': self.relpath(decl),
                'name_loc': (decl.location.line - 1, decl.location.column - 1),
                'decl_start_line': GccBugs.var_decl_start_loc(decl).line - 1,
                'external': decl.external,
                'public': decl.public,
                'static': decl.static,
                'decl_str': None,
            }

            # tricky skill to get right str_decl
            if decl.location.file in self.mod_srcs + self.sdcr_srcs:
                decl_str = decl.str_decl.split('=')[0].strip(' ;') + ';'
                decl_str = decl_str.replace('static ', 'extern ')
                properties['decl_str'] = GccBugs.fix(decl, decl_str)

            self.var_prop.append(properties)

    def collect_callback(self):
        """Collect all callback functions of the current source file"""

        # return True means we stop walk subtree
        def mark_callback(op, caller):
            if (isinstance(op, gcc.FunctionDecl)
                    and not self.decl_in_section(op, '.init.text')):
                self.cb_prop.append(list(self.decl_sig(op)))

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
            if (decl.initial and type_name != 'sched_class' and
                    not self.decl_in_section(decl, '.discard.addressable')):
                decl.initial.walk_tree(mark_callback, decl)

    def collect_struct(self):
        """Collect all struct definition information"""
        public_fields = defaultdict(set)

        def mark_public_field(op, node, parent_component_ref):
            if isinstance(op, gcc.ComponentRef):
                if isinstance(op.target, gcc.ComponentRef):
                    parent_component_ref[op.target] = op

                context = op.field.context
                while op.field.name is None and op in parent_component_ref:
                    op = parent_component_ref[op]

                loc_file = self.relpath(context.stub)
                if loc_file in self.mod_hdrs and context.name is not None:
                    """When acecssing 2 32bit fields at one time, the AST
                    ancestor is BitFieldRef. And op.field.name is None
                    """
                    field = op.field.name or '<unknown>'
                    public_fields[context].add((node.decl, field))

        for node in gcc.get_callgraph_nodes():
            # Ignore alias, it's insignificant at all
            if node.decl.function is None:
                continue
            for stmt in self.each_stmt(node):
                stmt.walk_tree(mark_public_field, node, {})

        def groupby(it, grouper, selector):
            sorted_list = sorted(it, key=grouper)
            return dict((k, list(map(selector, v)))
                        for k, v in _groupby(sorted_list, grouper))

        for struct, user_fields in public_fields.items():
            self.struct_prop[struct.name.name] = {
                'all_fields': [f.name for f in struct.fields if f.name],
                'public_fields': groupby(user_fields,
                    grouper=lambda user_field: user_field[1],
                    selector=lambda user_field: self.decl_sig(user_field[0]))
            }

    def collect_edge(self):
        """Collect all edges of the call graph"""
        for node in gcc.get_callgraph_nodes():
            if self.decl_in_section(node.decl, '.init.text'):
                continue

            # alias function
            if node.decl.function is None:
                alias = node.decl.attributes['alias'][0]
                real_name = alias.str_no_uid.replace('"', '')
                properties = {
                    "from": self.decl_sig(node.decl),
                    "to": (real_name, "?"),
                }
                self.edge_prop.append(properties)
                continue

            for stmt in self.each_call_stmt(node):
                if not stmt.fndecl:
                    continue
                assert node.decl.function
                properties = {
                    'from': self.decl_sig(node.decl),
                    'to': self.decl_sig(stmt.fndecl),
                }
                self.edge_prop.append(properties)

    def each_stmt(self, node):
        """Iterate each statement of call graph node"""
        for bb in node.decl.function.cfg.basic_blocks:
            if bb.gimple:
                for stmt in bb.gimple:
                    yield stmt

    def each_call_stmt(self, node):
        """Iterate each call statement of call graph node"""
        for bb in node.decl.function.cfg.basic_blocks:
            if not bb.gimple:
                continue
            stmts = list(bb.gimple)
            for i, stmt in enumerate(stmts):
                if isinstance(stmt, gcc.GimpleCall):
                    yield stmt

    def collect_info(self, p, _):
        """Collect information about the current source file"""
        if p.name != '*free_lang_data':
            return

        self.collect_fn()
        self.collect_callback()
        self.collect_struct()
        self.collect_edge()
        self.collect_var()

        collection = {
            'fn': self.fn_prop,
            'var': self.var_prop,
            'edge': self.edge_prop,
            'callback': self.cb_prop,
            'interface': self.intf_prop,
            'struct': self.struct_prop
        }

        with open(gcc.get_main_input_filename() + '.boundary', 'w') as f:
            json.dump(collection, f, indent=4)

    def register_cbs(self):
        """Register GCC Python Plugin callback"""
        gcc.register_callback(gcc.PLUGIN_PASS_EXECUTION, self.collect_info)


if __name__ == '__main__':
    import gcc

    # tmp directory to store middle files
    tmp_dir = gcc.argument_dict['tmpdir']
    collect = Collection(tmp_dir)
    collect.register_cbs()
