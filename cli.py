#!/usr/bin/env python3
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

"""cli.py - A command line interface for plugsched

Usage:
  plugsched-cli init        <release_kernel> <kernel_src> <work_dir>
  plugsched-cli dev_init    <kernel_src> <work_dir>
  plugsched-cli extract_src <kernel_src_rpm> <target_dir>
  plugsched-cli build       <work_dir>
  plugsched-cli (-h | --help)

Options:
  -h --help     Show help.

Available subcommands:
  init          Initialize a scheduler module for a specific kernel release and product
  dev_init      Initialize plugsched development envrionment from kernel source code
  extrat_src    extract kernel source code from kernel-src rpm
  build         Build a scheduler module rpm package for a specific kernel release and product

Subcommand arguments:
  release_kernel      `uname -r` of target kernel to be hotpluged
  kernel_src          kernel source code directory
  kernel_src_rpm      path of kernel source rpm
  work_dir            target working directory to develop new scheduler module
  target_dir          directory to place kernel source code
"""

import sys
from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    print("WARNING: YAML CLoader is not presented, it can be slow.")
    from yaml import Loader, Dumper
from docopt import docopt
import sh
from sh import rsync, cp, glob as _glob
from multiprocessing import cpu_count
from tempfile import mkdtemp
import coloredlogs
import logging
import uuid
import stat
import os
import re

def glob(pattern, _cwd='.'):
    return _glob(os.path.join(_cwd, pattern))

class ShutdownHandler(logging.StreamHandler):
    def emit(self, record):
        if record.levelno >= logging.CRITICAL:
            raise Exception("Fatal")

coloredlogs.install(level='INFO')
logging.getLogger().addHandler(ShutdownHandler())

class Plugsched(object):
    def __init__(self, work_dir, vmlinux, makefile):
        self.plugsched_path = os.path.dirname(os.path.realpath(__file__))
        self.work_dir = os.path.abspath(work_dir)
        self.vmlinux = os.path.abspath(vmlinux)
        self.makefile = os.path.abspath(makefile)
        self.mod_path = os.path.join(self.work_dir, 'kernel/sched/mod/')
        self.tmp_dir = os.path.join(self.work_dir, 'working/')
        plugsched_sh = sh(_cwd=self.plugsched_path)
        mod_sh = sh(_cwd=self.work_dir)
        self.plugsched_sh, self.mod_sh = plugsched_sh, mod_sh
        self.get_kernel_version(self.makefile)
        self.get_config_dir()
        self.search_springboard = sh.Command(self.plugsched_path + '/tools/springboard_search.sh')

        with open(os.path.join(self.config_dir, 'boundary.yaml')) as f:
            self.config = load(f, Loader)
        self.file_mapping = {
            self.config_dir + '/*':         self.tmp_dir,
            'boundary/*.py':                self.tmp_dir,
            'tools/symbol_resolve':         self.tmp_dir,
            'tools/springboard_search.sh':  self.tmp_dir,
            'src/Makefile.plugsched':       self.tmp_dir,
            'src/*.[ch]':                   self.mod_path,
            'src/Makefile':                 self.mod_path,
            'src/scheduler.lds':            self.mod_path,
            'src/.gitignore':               './',
        }
        self.threads = cpu_count()
        self.mod_files = self.config['mod_files']
        self.mod_srcs  = [f for f in self.mod_files if f.endswith('.c')]
        self.mod_hdrs  = [f for f in self.mod_files if f.endswith('.h')]
        self.sdcr      = [] if self.config['sidecar'] is None else self.config['sidecar']
        self.sdcr_srcs = [f[1] for f in self.sdcr]
        self.sdcr_objs = [os.path.basename(f).replace('.c', '.o') for f in self.sdcr_srcs]
        self.mod_objs  = [f+'.extract' for f in self.mod_files + self.sdcr_srcs]

    def get_kernel_version(self, makefile):
        VERSION = self.plugsched_sh.awk('-F=', '/^VERSION/{print $2}', makefile).strip()
        PATCHLEVEL = self.plugsched_sh.awk('-F=', '/^PATCHLEVEL/{print $2}', makefile).strip()
        SUBLEVEL = self.plugsched_sh.awk('-F=', '/^SUBLEVEL/{print $2}', makefile).strip()
        self.KVER = '%s.%s.%s' % (VERSION, PATCHLEVEL, SUBLEVEL)

        KREL = self.plugsched_sh.awk('-F=', '/^EXTRAVERSION/{print $2}', makefile).strip(' \n-')
        if len(KREL) == 0:
            logging.fatal('''Maybe you are using plugsched on non-released kernel,
                          please set EXTRAVERSION in Makefile (%s) before build kernel''',
                          os.path.join(self.work_dir, 'Makefile'))

        self.major = '%s.%s' % (VERSION, PATCHLEVEL)
        self.uname_r = '%s-%s' % (self.KVER, KREL)

        # strip ARCH
        for arch in ['.x86_64', '.aarch64']:
            idx = KREL.find(arch)
            if idx != -1: self.KREL = KREL[:idx]

    def get_config_dir(self):
        def common_prefix_len(s1, s2):
            for i, (a, b) in enumerate(zip(s1, s2)):
                if a != b:
                    break
            return i

        candidates = list(map(os.path.basename, glob('%s/configs/%s*' % (self.plugsched_path, self.major))))
        if len(candidates) == 0:
            logging.fatal('''Can't find config directory, please add config for kernel %s''', self.KVER)

        candidates.sort(reverse=True)
        _, idx = max((common_prefix_len(self.uname_r, t), i) for i, t in enumerate(candidates))

        logging.info("Choose config dir %s/" % candidates[idx])
        self.config_dir = os.path.join(self.plugsched_path, 'configs/', candidates[idx])

    def apply_patch(self, f, **kwargs):
        path = os.path.join(self.tmp_dir, f)
        if os.path.exists(path):
            self.mod_sh.patch(input=path, strip=1, _out=sys.stdout, _err=sys.stderr, **kwargs)

    def make(self, stage, objs=[], **kwargs):
        self.mod_sh.make(stage,
                         'objs=%s' % ' '.join(objs),
                         *['%s=%s' % i for i in kwargs.items()],
                         file=os.path.join(self.tmp_dir, 'Makefile.plugsched'),
                         jobs=self.threads,
                         _out=sys.stdout,
                         _err=sys.stderr)

    def extract(self):
        logging.info('Extracting scheduler module objs: %s', ' '.join(self.mod_objs))
        self.make(stage = 'collect', plugsched_tmpdir = self.tmp_dir, plugsched_modpath = self.mod_path)
        self.make(stage = 'analyze', plugsched_tmpdir = self.tmp_dir, plugsched_modpath = self.mod_path)
        self.make(stage = 'extract', plugsched_tmpdir = self.tmp_dir, plugsched_modpath = self.mod_path,
                  objs = self.mod_objs)

    def create_sandbox(self, kernel_src):
        logging.info('Creating mod build directory structure')
        rsync(kernel_src + '/', self.work_dir, archive=True, verbose=True, delete=True, exclude='.git', filter=':- .gitignore')
        self.mod_sh.mkdir(self.mod_path, parents=True)
        self.mod_sh.mkdir(self.tmp_dir, parents=True)

        for f, t in self.file_mapping.items():
            self.mod_sh.cp(glob(f, _cwd=self.plugsched_path), t, recursive=True, dereference=True)

    def delete_export_symbol(self):
        delete_patt = re.compile('EXPORT_.*SYMBOL')
        for src_f in self.mod_files + self.sdcr_srcs:
            with open(os.path.join(self.work_dir, src_f), 'r+') as f:
                lines = f.readlines()
                for (i, line) in enumerate(lines):
                    if not delete_patt.search(line):
                        continue

                    # handle backslash(\) corner case, for e.g:
                    # #define BPF_TRACE_DEFN_x(x)                \
                    #        void bpf_trace_run##x();            \
                    #        EXPORT_SYMBOL_GPL(bpf_trace_run##x)
                    prev = lines[i-1] if i > 0 else ''
                    if prev.endswith('\\\n') and not line.endswith('\\\n'):
                        lines[i-1] = prev[:-2].rstrip() + '\n'
                    lines[i] = ''

                f.seek(0)
                f.truncate()
                f.writelines(lines)

    def cmd_init(self, kernel_src, sym_vers, kernel_config):
        self.create_sandbox(kernel_src)
        self.plugsched_sh.cp(sym_vers,      self.work_dir, force=True)
        self.plugsched_sh.cp(kernel_config, self.work_dir + '/.config', force=True)
        self.plugsched_sh.cp(self.makefile, self.work_dir, force=True)
        self.plugsched_sh.cp(self.vmlinux,  self.work_dir, force=True)

        logging.info('Patching kernel with pre_extract patch')
        self.apply_patch('pre_extract.patch')
        self.delete_export_symbol()
        self.extract()
        logging.info('Patching extracted scheduler module with post_extractd patch')
        self.apply_patch('post_extract.patch')
        logging.info('Patching dynamic springboard')
        self.apply_patch('dynamic_springboard.patch')

        with open(os.path.join(self.mod_path, 'Makefile'), 'a') as f:
            self.search_springboard('init', self.vmlinux, kernel_config, _out=f)

        logging.info("Succeed!")

    # when python3 working with rpmbuild, the /usr/local/python* path
    # won't be in included in sys/path which results in some modules
    # can't be find. So we need to add the PYTHONPATH manually.
    # The detail about this can be find in
    # https://fedoraproject.org/wiki/Changes/Making_sudo_pip_safe
    def add_python_path(self):
        py_ver = sys.version[0:3]
        python_path = '/usr/local/lib64/python' + py_ver + '/site-packages'
        python_path += os.pathsep
        python_path += '/usr/local/lib/python' + py_ver + '/site-packages'
        os.environ["PYTHONPATH"] = python_path

    def cmd_build(self):
        if not os.path.exists(self.work_dir):
            logging.fatal("plugsched: Can't find %s", self.work_dir)
        self.add_python_path()
        logging.info("Preparing rpmbuild environment")
        rpmbuild_root = os.path.join(self.plugsched_path, 'rpmbuild')
        self.plugsched_sh.rm('rpmbuild', recursive=True, force=True)
        self.plugsched_sh.mkdir('rpmbuild')
        rpmbase_sh = sh(_cwd=rpmbuild_root)
        rpmbase_sh.mkdir(['BUILD','RPMS','SOURCES','SPECS','SRPMS'])

        self.plugsched_sh.cp('module-contrib/scheduler.spec', os.path.join(rpmbuild_root, 'SPECS'), force=True)
        rpmbase_sh.rpmbuild('--define', '%%_outdir %s' % os.path.realpath(self.plugsched_path + '/module-contrib'),
                            '--define', '%%_topdir %s' % os.path.realpath(rpmbuild_root),
                            '--define', '%%_dependdir %s' % os.path.realpath(self.plugsched_path),
                            '--define', '%%_kerneldir %s' % os.path.realpath(self.work_dir),
                            '--define', '%%_tmpdir %s' % self.tmp_dir,
                            '--define', '%%_modpath %s' % self.mod_path,
                            '--define', '%%_sdcrobjs %s' % ' '.join(self.sdcr_objs) + '""',
                            '--define', '%%KVER %s' % self.KVER,
                            '--define', '%%KREL %s' % self.KREL,
                            '--define', '%%threads %d' % self.threads,
                            '-bb', 'SPECS/scheduler.spec',
                            _out=sys.stdout,
                            _err=sys.stderr)
        logging.info("Succeed!")

if __name__ == '__main__':
    arguments = docopt(__doc__)

    if arguments['extract_src']:
        kernel_src_rpm = arguments['<kernel_src_rpm>']
        target_dir = arguments['<target_dir>']

        rpmbuild_root = mkdtemp()
        sh.rpmbuild('--define', '%%_topdir %s' % rpmbuild_root,
                    '--define', '%%__python %s' % '/usr/bin/python3',
                    '-rp', '--nodeps', kernel_src_rpm)

        src = glob('kernel*/linux*', rpmbuild_root + '/BUILD/')

        if len(src) != 1:
            logging.fatal("find multi kernel source, fuzz ...")

        rsync(src[0] + '/', target_dir + '/', archive=True, verbose=True, delete=True)

        # certificates for CONFIG_MODULE_SIG_KEY & CONFIG_SYSTEM_TRUSTED_KEYS
        for pem in glob('*.pem', rpmbuild_root + '/SOURCES/'):
            sh.cp(pem, target_dir + '/certs', force=True)

        sh.rm(rpmbuild_root, recursive=True, force=True)

    elif arguments['init']:
        release_kernel = arguments['<release_kernel>']
        kernel_src = arguments['<kernel_src>']
        work_dir = arguments['<work_dir>']

        vmlinux = '/usr/lib/debug/lib/modules/' + release_kernel + '/vmlinux'
        if not os.path.exists(vmlinux):
            logging.fatal("%s not found, please install kernel-debuginfo-%s.rpm", vmlinux, release_kernel)

        sym_vers      = '/usr/src/kernels/' + release_kernel + '/Module.symvers'
        kernel_config = '/usr/src/kernels/' + release_kernel + '/.config'
        makefile      = '/usr/src/kernels/' + release_kernel + '/Makefile'

        if not os.path.exists(kernel_config):
            logging.fatal("%s not found, please install kernel-devel-%s.rpm", kernel_config, release_kernel)

        plugsched = Plugsched(work_dir, vmlinux, makefile)
        plugsched.cmd_init(kernel_src, sym_vers, kernel_config)

    elif arguments['dev_init']:
        kernel_src = arguments['<kernel_src>']
        work_dir = arguments['<work_dir>']

        if not os.path.exists(kernel_src):
            logging.fatal("Kernel source directory not exists")

        vmlinux = os.path.join(kernel_src, 'vmlinux')
        if not os.path.exists(vmlinux):
            logging.fatal("%s not found, please execute `make -j %s` firstly", vmlinux, cpu_count())

        sym_vers      = os.path.join(kernel_src, 'Module.symvers')
        kernel_config = os.path.join(kernel_src, '.config')
        makefile      = os.path.join(kernel_src, 'Makefile')

        if not os.path.exists(kernel_config):
            logging.fatal("kernel config %s not found", kernel_config)

        plugsched = Plugsched(work_dir, vmlinux, makefile)
        plugsched.cmd_init(kernel_src, sym_vers, kernel_config)

    elif arguments['build']:
        work_dir = arguments['<work_dir>']

        vmlinux = os.path.join(work_dir, 'vmlinux')
        makefile = os.path.join(work_dir, 'Makefile')
        plugsched = Plugsched(work_dir, vmlinux, makefile)
        plugsched.cmd_build()

