#!/usr/bin/env python3
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from collections import defaultdict
from itertools import groupby as _groupby
from yaml import load, dump, resolver, CLoader as Loader, CDumper as Dumper
import json
import re
import os
import sys

# Use set as the default sequencer for yaml
Loader.add_constructor(resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
                     lambda loader, node: set(loader.construct_sequence(node)))
Dumper.add_representer(set, lambda dumper, node: dumper.represent_list(node))

# tmp directory to store middle files
tmpdir = None

# directory to store schedule module source code
modpath = None

class Extraction(object):
    def __init__(self, src_file, tmpdir, modpath):
        with open(tmpdir + 'boundary_extract.yaml') as f:
            self.config = load(f, Loader)

        self.src_file = src_file
        self.modpath = modpath
        self.mod_files = self.config['mod_files']
        self.mod_srcs = {f for f in self.mod_files if f.endswith('.c')}
        self.mod_hdrs = self.mod_files - self.mod_srcs
        self.sdcr_srcs = [f[1] for f in self.config['sidecar']]
        self.fn_list = []
        self.callback_list = []
        self.interface_list = []
        self.var_list = []

        if src_file in self.mod_hdrs:
            file_name = tmpdir + 'header_symbol.json'
        else:
            file_name = src_file + '.boundary'

        with open(file_name) as f:
            metas = json.load(f)
            self.meta_fn = metas['fn']
            self.meta_var = metas['var']

    def function_location(self):
        unique = set()
        for fn in self.meta_fn:
            # filter out *.h in *.c
            if fn['file'] != self.src_file: continue

            # remove duplicated function
            obj = tuple(fn['signature'])
            if obj in unique: continue
            unique.add(obj)

            if obj in self.config['function']['sched_outsider'] or \
               obj in self.config['function']['sdcr_out']:
                self.fn_list.append(fn),
            elif obj in self.config['function']['callback']:
                self.callback_list.append(fn)
            elif obj in self.config['function']['interface']:
                self.interface_list.append(fn)

    def var_location(self):
        # sidecar shares all global variables with vmlinux
        if self.src_file in self.sdcr_srcs:
            self.var_list = self.meta_var
            return

        for var in self.meta_var:
            if var['file'] != self.src_file:
                continue
            if var['name'] in self.config['global_var']['force_private']:
                continue
            # share public (non-static) variables by default
            if var['public'] or var['name'] in self.config['global_var']['extra_public']:
                self.var_list.append(var)

    # merge multi decl lines and return the merged line number
    def merge_up_lines(self, lines, curr):
        terminator = re.compile(';|}|#|//|\*/|^\n$')
        merged = lines[curr].strip()

        while curr >= 1:
            line = lines[curr-1]
            if terminator.search(line):
                break;
            merged = line.strip() + ' ' + merged
            lines[curr] = ''
            curr -= 1

        lines[curr] = merged.replace(' ;', ';') + '\n'
        return curr;

    def function_extract(self, lines):
        for fn in self.fn_list:
            name, inline = fn['name'], fn['inline']
            (row_start,col_start), (row_end,_) = fn['l_brace_loc'], fn['r_brace_loc']

            if inline or \
                    tuple(fn['signature']) in self.config['function']['optimized_out']:
                lines[row_end] += "/* DON'T MODIFY FUNCTION {}, IT'S NOT PART OF SCHEDMOD */\n".format(name)
            else:
                # convert function body "{}" to ";"
                # only handle normal kernel function definition
                lines[row_start] = lines[row_start][:col_start] + ";\n"
                self.merge_up_lines(lines, row_start)
                for i in range(row_start+1, row_end+1):
                    lines[i] = ''

        callback_export_c_fmt = "extern {ret} {fn}({params});\n"
        for fn in self.callback_list:
            name, decl_str = fn['name'], fn['decl_str']
            (row_start,col_start), (row_end,_) = fn['name_loc'], fn['r_brace_loc']

            new_name = '__mod_' + name
            lines[row_start] = lines[row_start][:col_start] + \
                lines[row_start][col_start:].replace(name, '__used ' + new_name)
            lines[row_end] = lines[row_end] + '\n' + \
                "/* DON'T MODIFY SIGNATURE OF FUNCTION {}, IT'S CALLBACK FUNCTION */\n".format(new_name) + \
                callback_export_c_fmt.format(**decl_str)

        for fn in self.interface_list:
            name, decl_str, (row_end,_) = fn['name'], fn['decl_str'], fn['r_brace_loc']

            # everyone know that syscall ABI should be consistent
            if any(name.startswith(prefix) for prefix in self.config['interface_prefix']):
                continue
            lines[row_end] += \
                "/* DON'T MODIFY SIGNATURE OF FUNCTION {}, IT'S INTERFACE FUNCTION */\n".format(name)

    def merge_down_lines(self, lines, curr):
            next = curr
            merged = lines[curr].strip()
            l_paren = merged.count('(')
            r_paren = merged.count(')')

            while l_paren > r_paren:
                next += 1
                line = lines[next]
                merged  += line.strip()
                r_paren += line.count(')')
                lines[next] = ''

            lines[curr] = merged + '\n'
            return curr

    def var_extract(self, lines):
        # General handling all shared variables
        orig_lines = list(lines)
        for var in list(self.var_list):
            name, row_start, row_end = var['name'], var['decl_start_line'], var['decl_end_line']

            # merge multi-var-definition lines into one
            self.merge_down_lines(lines, row_start)

            # delete data initialization code
            for i in range(row_start+1, row_end+1):
                lines[i] = ''

            # Specially handling shared per_cpu and static_key variables to improve readability
            line = orig_lines[row_start]
            if 'DEFINE_PER_CPU(' in line:
                line = line.replace('DEFINE_PER_CPU(', 'DECLARE_PER_CPU(').replace('static ', '')
            elif 'DEFINE_PER_CPU_SHARED_ALIGNED(' in line:
                line = line.replace('DEFINE_PER_CPU_SHARED_ALIGNED(', 'DECLARE_PER_CPU_SHARED_ALIGNED(').replace('static ', '')
            elif 'DEFINE_STATIC_KEY_FALSE(' in line:
                line = line.replace('DEFINE_STATIC_KEY_FALSE(', 'DECLARE_STATIC_KEY_FALSE(').replace('static ', '')
            elif 'DEFINE_STATIC_KEY_TRUE(' in line:
                line = line.replace('DEFINE_STATIC_KEY_TRUE(', 'DECLARE_STATIC_KEY_TRUE(').replace('static ', '')
            elif 'EXPORT_' in line and '_SYMBOL' in line:
                line = ''
            else:
                # delete data definition
                lines[row_start] = ''
                continue

            lines[row_start] = line
            self.var_list.remove(var)

        # convert data definition to declarition
        for var in self.var_list:
            row_start = var['decl_start_line']
            lines[row_start] += var['decl_str'] + '\n'

    # fix trival code adaption
    def fix_up(self, lines):
        delete_patt = re.compile('initcall|early_param|__init |__initdata |__setup')
        replace_list = [('struct atomic_t', 'atomic_t')]

        for (i, line) in enumerate(lines):
            # fixup header file path, assume there is one include per line
            if '#include "' in line:
                old_header = line.split('"')[1]
                rel_header = os.path.join(os.path.dirname(self.src_file), old_header)
                rel_header = os.path.relpath(rel_header)
                # module header file is extracted to the right place
                if rel_header in self.mod_files: continue

                lines[i] = line.replace(old_header, os.path.relpath(rel_header, self.modpath))
                continue

            if delete_patt.search(line):
                lines[i] = ''
                continue

            for (p, repl) in replace_list:
                if p in line:
                    lines[i] = line.replace(p, repl)
                    break;

    def extract_file(self):
        src_f = self.src_file
        self.function_location()
        self.var_location()

        with open(src_f) as in_f, open(self.modpath + os.path.basename(src_f), 'w') as out_f:
            lines = in_f.readlines()
            self.function_extract(lines)
            self.var_extract(lines)
            self.fix_up(lines)
            out_f.writelines(lines)

if __name__ == '__main__':

    src_file = sys.argv[1]
    tmpdir = sys.argv[2]
    modpath = sys.argv[3]
    extract = Extraction(src_file, tmpdir, modpath)
    extract.extract_file()
