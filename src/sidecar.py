from sh import cat, awk
import sys
from os.path import basename
import process

if __name__ == '__main__':
    f = cat(sys.argv[1])
    symfile = awk(f, source='/^EXPORT_SIDECAR/{print $2, $3}', field_separator='[,(]').splitlines()
    functions = [tuple(line.split()) for line in symfile]

    process.config = {
        'mod_files': [f for _, f in functions],
        'mod_files_basename': {basename(f): f for _, f in functions},
        'mod_header_files': []
    }
    process.func_class = {
        'fn': functions
    }
    process.find_in_vmlinux(sys.argv[2])

    sympos = process.local_sympos
    sympos = {fn[0]: 0 if fn not in sympos else sympos[fn] for fn in functions}

    with open('undefined_functions_sidecar.h', 'w') as f:
        for fn, pos in sympos.iteritems():
            f.write('{"%s", %d},\n' % (fn, pos))
    with open('tainted_functions_sidecar.h', 'w') as f:
        for fn, pos in sympos.iteritems():
            f.write('TAINTED_FUNCTION(%s, %d)\n' % (fn, pos))
