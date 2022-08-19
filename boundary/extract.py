#!/usr/bin/env python3
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
"""Extract module code according to boundary information"""

import json
import re
import os
import sys
from yaml import load, resolver, CLoader as Loader

# Use set as the default sequencer for yaml
Loader.add_constructor(
    resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
    lambda loader, node: set(loader.construct_sequence(node)))


class Extraction(object):

    def __init__(self, src_file, tmp_dir, mod_dir):
        with open(tmp_dir + 'boundary_extract.yaml') as f:
            self.config = load(f, Loader)

        self.src_file = src_file
        self.mod_dir = mod_dir
        self.mod_files = self.config['mod_files']
        self.mod_srcs = {f for f in self.mod_files if f.endswith('.c')}
        self.mod_hdrs = self.mod_files - self.mod_srcs
        self.sdcr_srcs = [f[1] for f in self.config['sidecar']]
        self.fn_list = []
        self.callback_list = []
        self.interface_list = []
        self.var_list = []

        if src_file in self.mod_hdrs:
            file_name = tmp_dir + 'header_symbol.json'
        else:
            file_name = src_file + '.boundary'

        with open(file_name) as f:
            metas = json.load(f)
            self.meta_fn = metas['fn']
            self.meta_var = metas['var']

    def function_location(self):
        """Get the source code location of border functions"""
        unique = set()
        for fn in self.meta_fn:
            # filter out *.h in *.c
            if fn['file'] != self.src_file:
                continue

            # remove duplicated function
            obj = tuple(fn['signature'])
            if obj in unique:
                continue
            unique.add(obj)

            if (obj in self.config['function']['sched_outsider'] or
                    obj in self.config['function']['sdcr_out']):
                self.fn_list.append(fn)
            elif obj in self.config['function']['callback']:
                self.callback_list.append(fn)
            elif obj in self.config['function']['interface']:
                self.interface_list.append(fn)

    def var_location(self):
        """Get the source code location of shared global variables"""
        # sidecar shares all global variables with vmlinux
        if self.src_file in self.sdcr_srcs:
            self.var_list = self.meta_var
            return

        for var in self.meta_var:
            if var['file'] != self.src_file or var['external']:
                continue
            if var['name'] in self.config['global_var']['force_private']:
                continue
            # share public (non-static) variables by default
            if (var['public'] or
                    var['name'] in self.config['global_var']['extra_public']):
                self.var_list.append(var)

    def merge_up_lines(self, lines, curr):
        """Merge up multi-lines-function-declaration into one line"""
        terminator = re.compile(';|}|#|//|\*/|^\n$')
        merged = lines[curr].strip()

        while curr >= 1:
            line = lines[curr - 1]
            if terminator.search(line):
                break
            merged = line.strip() + ' ' + merged
            lines[curr] = ''
            curr -= 1

        lines[curr] = merged.replace(' ;', ';') + '\n'
        return curr

    def function_extract(self, lines):
        """Generate function code for new module"""
        warn = "/* DON'T MODIFY INLINE EXTERNAL FUNCTION {} */\n"
        cb_warn = "/* DON'T MODIFY SIGNATURE OF CALLBACK FUNCTION {} */\n"
        if_warn = "/* DON'T MODIFY SIGNATURE OF INTERFACE FUNCTION {} */\n"
        decl_fmt = "extern {ret} {fn}({params});\n"

        for fn in self.fn_list:
            name, inline = fn['name'], fn['inline']
            (row_end, _) = fn['r_brace_loc']
            (row_start, col_start) = fn['l_brace_loc']

            if (inline or tuple(fn['signature'])
                    in self.config['function']['optimized_out']):
                lines[row_end] += warn.format(name)
            else:
                # convert function body "{}" to ";"
                # only handle normal kernel function definition
                lines[row_start] = lines[row_start][:col_start] + ";\n"
                self.merge_up_lines(lines, row_start)
                for i in range(row_start + 1, row_end + 1):
                    lines[i] = ''

        for fn in self.callback_list:
            name, decl_str = fn['name'], fn['decl_str']
            (row_start, _) = fn['name_loc']
            (row_end, _) = fn['r_brace_loc']
            new_name = '__cb_' + name
            used_name = '__used ' + new_name

            lines[row_start] = lines[row_start].replace(name, used_name)
            lines[row_end] += ('\n' + cb_warn.format(new_name) +
                               decl_fmt.format(**decl_str))

        for fn in self.interface_list:
            name, (row_end, _) = fn['name'], fn['r_brace_loc']

            # everyone know that syscall ABI should be consistent
            if any(name.startswith(prefix)
                   for prefix in self.config['interface_prefix']):
                continue
            lines[row_end] += if_warn.format(name)

    def merge_down_lines(self, lines, curr):
        """Merge down multi-lines-var-definition into one line"""
        merged = ''
        start = curr

        while curr < len(lines) and ';' not in lines[curr]:
            merged += lines[curr].strip() + ' '
            lines[curr] = ''
            curr += 1

        merged += lines[curr]
        lines[curr] = ''
        lines[start] = merged
        return curr

    def var_extract(self, lines):
        """Generate data declarition code for new module"""
        # General handling all shared variables
        for var in list(self.var_list):
            name, row_start = var['name'], var['decl_start_line']
            (row_name, _) = var['name_loc']

            # Fixed variable name not on first line, e.g. nohz
            for i in range(row_start + 1, row_name):
                lines[i] = ''

            self.merge_down_lines(lines, row_start)

            # Specially handling shared per_cpu and static_key variables
            # to improve readability
            line = lines[row_start]
            replace_list = [
                ('DEFINE_PER_CPU', 'DECLARE_PER_CPU'),
                ('DEFINE_STATIC_KEY', 'DECLARE_STATIC_KEY'),
            ]

            for (p, repl) in replace_list:
                if p in line:
                    line = line.replace(p, repl).replace('static ', '')
                    lines[row_start] = line
                    self.var_list.remove(var)
                    break

        # delete data definition
        for var in self.var_list:
            row_start = var['decl_start_line']
            lines[row_start] = ''

        # convert data definition to declarition
        for var in self.var_list:
            row_start = var['decl_start_line']
            lines[row_start] += var['decl_str'] + '\n'

    def fix_include(self, line):
        """Fix header file path, assume one include per line"""
        old_header = line.split('"')[1]
        rel_header = os.path.join(os.path.dirname(self.src_file), old_header)
        rel_header = os.path.relpath(rel_header)

        # module header file is already extracted to the right place
        if rel_header in self.mod_files:
            return line

        new_header = os.path.relpath(rel_header, self.mod_dir)
        return line.replace(old_header, new_header)

    def fix_up(self, lines):
        """Post fix trival code adaption"""
        delete = re.compile('initcall|early_param|__init |__initdata |__setup')
        replace_list = [('struct atomic_t', 'atomic_t')]

        for (i, line) in enumerate(lines):
            if '#include "' in line:
                lines[i] = self.fix_include(line)
                continue

            if delete.search(line):
                lines[i] = ''
                continue

            for (p, repl) in replace_list:
                if p in line:
                    lines[i] = line.replace(p, repl)
                    break

    def extract_file(self):
        """Generate module source code"""
        self.function_location()
        self.var_location()

        src_f = self.src_file
        res_f = self.mod_dir + os.path.basename(src_f)

        with open(src_f) as in_f, open(res_f, 'w') as out_f:
            lines = in_f.readlines()
            self.function_extract(lines)
            self.var_extract(lines)
            self.fix_up(lines)
            out_f.writelines(lines)


if __name__ == '__main__':

    src_file = sys.argv[1]
    # tmp directory to store middle files
    tmp_dir = sys.argv[2]
    # directory to store schedule module source code
    mod_dir = sys.argv[3]
    extract = Extraction(src_file, tmp_dir, mod_dir)
    extract.extract_file()
