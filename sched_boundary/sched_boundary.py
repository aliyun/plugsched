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

class SchedBoundary(object):
    def __init__(self, config):
        with open(config) as f:
            self.config = load(f, Loader)
        self.sched_mod_files = self.config['mod_files']
        self.sched_mod_source_files = {f for f in self.sched_mod_files if f.endswith('.c')}
        self.sched_mod_header_files = self.sched_mod_files - self.sched_mod_source_files

    def process_passes(self, p, _):
        if p.name != '*free_lang_data':
            return
        self.final_work()
        # Exit early, so no real .o files are created. This speed up whole process a lot.
        # Also, this allows different stages always compile all files.
        os._exit(0)

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
    array_pointer_re = re.compile(r'(.*)\[([0-9]*)\] \* (.*);')

    # struct cpumask[1] * doms_cur; -> struct cpumask (*doms_cur)[1];
    @staticmethod
    def array_pointer(decl, str):
        return GccBugs.array_pointer_re.sub(r'\1 (*\3)[\2];', str)

    @staticmethod
    def enum_type_name(decl, str):
        if isinstance(decl.type, gcc.EnumeralType):
            i = str.find(decl.type.name.name)
            return str[:i] + 'enum ' + str[i:]
        else:
            return str

class SchedBoundaryExtract(SchedBoundary):
    def __init__(self):
        super().__init__('sched_boundary_extract.yaml')
        assert gcc.get_main_input_filename() in self.sched_mod_source_files
        self.fn_list = []
        self.fn_ptr_list = []
        self.var_list = []

    # TODO use gcc.get_callgraph_nodes is okay too. Are there any difference ?
    def function_define(self, decl, _):
        # only func definition will trigger PLUGIN_FINISH_PARSE_FUNCTION
        loc = gcc.get_location()

        # filter out *.h
        if loc.file != gcc.get_main_input_filename():
            return

        assert(isinstance(decl, gcc.FunctionDecl))

        fn_ptr_export_fmt = "PLUGSCHED_FN_PTR({fn}, {ret}, {params})\n"
        # translate index to start with 0
        if decl.name in self.config['function']['outsider'] or \
           decl.name in self.config['function']['init']:
            self.fn_list.append([decl,
                decl.function.start.line - 1,
                decl.function.start.column - 1,
                decl.function.end.line - 1,
                decl.function.end.column - 1])
        elif decl.name in self.config['function']['fn_ptr']:
            fn_ptr_export = fn_ptr_export_fmt.format(
                fn=decl.name,
                ret=GccBugs.enum_type_name(decl.result, decl.result.type.str_no_uid),
                params=",".join(GccBugs.enum_type_name(arg, arg.type.str_no_uid) for arg in decl.arguments)
            )
            self.fn_ptr_list.append([decl,
                fn_ptr_export,
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

        if decl.name in self.config['global_var']['public']:
            if anonymous_type_var(decl):
                self.var_list.append((decl,
                    decl.type.stub.location.line - 1,
                    loc.line - 1))
            else:
                self.var_list.append((decl,
                    decl.location.line - 1,
                    loc.line - 1))

    def final_work(self):
        src_fn = gcc.get_main_input_filename()
        assert src_fn in self.sched_mod_source_files

        with open(src_fn) as in_f, open(src_fn + '.fn_ptr.h', 'w') as fn_ptr_f:
            lines = in_f.readlines()

            for decl, fn_row_start, fn_col_start, fn_row_end, __ in self.fn_list:
                if 'always_inline' in decl.attributes or decl.inline is True or decl.name in self.config['function']['optimized_out']:
                    lines[fn_row_end] += " /* DON'T MODIFY FUNCTION {}, IT'S NOT PART OF SCHEDMOD */\n".format(decl.name)
                else:
                    # convert function body "{}" to ";"
                    # only handle normal kernel function definition
                    lines[fn_row_start] = lines[fn_row_start][: fn_col_start] + ";\n"
                    for i in range(fn_row_start+1, fn_row_end+1):
                        lines[i] = ''

            for decl, export, fn_row_start, fn_col_start, fn_row_end, fn_col_end in self.fn_ptr_list:
                fn_ptr_f.write(export)
                decl.public = True
                decl.external = True
                decl.static = False
                lines[fn_row_start] = lines[fn_row_start][:fn_col_start] + \
                    lines[fn_row_start][fn_col_start:].replace(decl.name, '__mod_' + decl.name)
                lines[fn_row_end] = lines[fn_row_end] + '\n' + \
                    GccBugs.enum_type_name(decl.result, decl.str_decl) + '\n'

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
                    line = decl.str_decl
                    line = GccBugs.array_pointer(decl, line)
                    line = GccBugs.enum_type_name(decl, line)
                    line += '\n'
                lines[row_start] = line
                for i in range(row_start+1, row_end+1):
                    lines[i] = ''

            with open("kernel/sched/mod/" + os.path.basename(src_fn), 'w') as out_f:
                out_f.writelines(lines)

class SchedBoundaryCollect(SchedBoundary):
    def __init__(self):
        super().__init__('sched_boundary.yaml')
        self.fn_properties = []
        self.var_properties = []
        self.edge_properties = []
        self.struct_properties = {}
        self.fn_ptr_properties = []
        self.seek_public_field = False

    def is_init_fn(self, fndecl):
        attr = fndecl.attributes
        if attr and '__section__' in attr:
            assert len(attr['__section__']) == 1
            if attr['__section__'][0].str_no_uid == '".init.text"':
                return True
        return False

    def include_file(self, header, _):
        if header in self.sched_mod_header_files:
            self.seek_public_field = True

    def collect_fn(self):
        for node in gcc.get_callgraph_nodes():
            decl = node.decl
            if not isinstance(decl.context, gcc.TranslationUnitDecl):
                continue
            # Ignore alias function for now ??
            if decl.function is None:
                continue
            properties = {
                "name": decl.name,
                "init": self.is_init_fn(decl),
                "syscall": any(decl.name.startswith(prefix) for prefix in self.config['interface_prefix']),
                "file": decl.location.file,
                "l_brace_loc": (decl.function.start.line, decl.function.start.column),
                "r_brace_loc": (decl.function.end.line, decl.function.end.column),
                "fn_name_loc": (decl.location.line, decl.location.column),
                "external": decl.external,
                "public": decl.public,
                "static": decl.static,
            }
            self.fn_properties.append(properties)

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
            "file": decl.location.file,
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
            if isinstance(op, gcc.FunctionDecl) and not self.is_init_fn(op):
                self.fn_ptr_properties.append({
                    'name': op.name,
                    'file': op.location.file if op.function else '?'
                })

        def temporary_var(var):
            if 'section' in var.attributes and var.attributes['section'][0].constant == '.discard.addressable':
                assert len(var.attributes['section']) == 1
                return True
            return False

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
            if var.decl.initial and not temporary_var(var.decl):
                var.decl.initial.walk_tree(mark_fn_ptr, var.decl)

    def collect_struct(self):
        public_fields = defaultdict(set)

        def mark_public_field(op, node):
            if isinstance(op, gcc.ComponentRef) and \
               op.field.context.stub.location.file in self.sched_mod_header_files and \
               op.field.context.name is not None:
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
                                         selector=lambda (user, field): {
                                            'name': user.name,
                                            'file': user.location.file
                                         })
            }

    def collect_edges(self):
        def wait_arg(op, _):
            if isinstance(op, (gcc.VarDecl, gcc.FieldDecl)) and op.type and op.type.name \
               and op.type.name.name in ('mutex', 'wait_queue_head_t'):
                return True
            return False

        for node in gcc.get_callgraph_nodes():
            if self.is_init_fn(node.decl):
                continue

            # alias function
            if node.decl.function is None:
                real_name = node.decl.attributes['alias'][0].str_no_uid.replace('"','')
                properties = {
                    "from": {"name": node.decl.name, "file": node.decl.location.file},
                    "to": {"name": real_name, "file": "?"},
                    "tail": True,
                    "carry": None
                }
                self.edge_properties.append(properties)
                continue

            for stmt, tail in self.each_call_stmt(node):
                if not stmt.fndecl:
                    continue
                callee_carry_wait = stmt.walk_tree(wait_arg, None)
                assert node.decl.function
                properties = {
                    "from": {
                        "name": node.decl.name,
                        "file": node.decl.location.file
                    },
                    "to": {
                        "name": stmt.fndecl.name,
                        "file": stmt.fndecl.location.file if stmt.fndecl.function else '?'
                    },
                    "carry": [callee_carry_wait.context.name.name, callee_carry_wait.name] if isinstance(callee_carry_wait, gcc.FieldDecl) else
                             [callee_carry_wait.name] if isinstance(callee_carry_wait, gcc.VarDecl) else
                             None,
                    "tail": tail
                }
                self.edge_properties.append(properties)

    def each_stmt(self, node):
        for bb in node.decl.function.cfg.basic_blocks:
            if bb.gimple:
                for stmt in bb.gimple:
                    yield stmt

    def each_call_stmt(self, node):
        # If is "return [retval]"
        def ret_gimple_seq(gimples):
            for g in gimples:
                if isinstance(g, gcc.GimpleAssign):
                    if not isinstance(g.lhs, gcc.VarDecl):
                        return False
                    if g.lhs.context != node.decl:
                        return False
                elif isinstance(g, gcc.GimpleLabel):
                    continue
                elif isinstance(g, gcc.GimpleReturn):
                    return True
                else:
                    return False
            return False

        for bb in node.decl.function.cfg.basic_blocks:
            if not bb.gimple:
                continue
            stmts = list(bb.gimple)
            succs = []
            if not bb.succs:
                # It's actually unreachable, eg. panic, BUG_ON, but we use GimpleReturn to workaround
                succs = [gcc.GimpleReturn()]
            elif len(bb.succs) == 1:
                # Call gimple should not have branch
                succs = bb.succs[0].dest.gimple
            for i, stmt in enumerate(stmts):
                if isinstance(stmt, gcc.GimpleCall):
                    yield stmt, ret_gimple_seq(stmts[i+1:] + succs)

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
            "struct": self.struct_properties
        }
        with open(gcc.get_main_input_filename() + '.sched_boundary', 'w') as f:
            json.dump(collect, f, indent=4)

if __name__ == '__main__':
    import gcc

    stage = gcc.argument_dict['stage']

    if gcc.get_main_input_filename() == 'scripts/mod/empty.c':
        exit(0)

    if stage == 'extract':
        sched_boundary = SchedBoundaryExtract()
    elif stage == "collect":
        sched_boundary = SchedBoundaryCollect()
    else:
        raise Exception("")

    sched_boundary.register_cbs()
