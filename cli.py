#!/usr/bin/env python2

import sys
from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    print >> sys.stderr, "WARNING: YAML CLoader is not presented, it can be slow."
    from yaml import Loader, Dumper
import sh
from sh import rsync, cp, glob as _glob
from sched_boundary import check_sym_duplicy
from sanity import SanityChecker
import coloredlogs
import logging
import uuid
import stat
import fire
import os

def glob(pattern, _cwd='.'):
    return _glob(os.path.join(_cwd, pattern))

class ShutdownHandler(logging.StreamHandler):
    def emit(self, record):
        if record.levelno >= logging.CRITICAL:
            raise Exception("Fatal")

coloredlogs.install(level='INFO')
logging.getLogger().addHandler(ShutdownHandler())

class Plugsched(object):
    def __init__(self, kernel_path, mod_path, threads, kernel_debuginfo_path='', kernel_devel_path=''):
        self.plugsched_path = os.path.abspath(os.path.dirname(__file__))
        self.kernel_path = kernel_path
        self.mod_path = mod_path
        self.kernel_debuginfo_path = kernel_debuginfo_path
        self.kernel_devel_path = kernel_devel_path
        self.search_springboard = sh.Command(os.path.join(self.plugsched_path, 'springboard_search.sh')) \
                                    .bake(_cwd=self.kernel_debuginfo_path)

        plugsched_sh = sh(_cwd=self.plugsched_path)
        kernel_sh = sh(_cwd=self.kernel_path)
        mod_sh = sh(_cwd=self.mod_path)

        self.plugsched_sh, self.kernel_sh, self.mod_sh = plugsched_sh, kernel_sh, mod_sh

        with open(os.path.join(self.plugsched_path, 'sched_boundary/sched_boundary.yaml')) as f:
            self.config = load(f, Loader)
        self.file_mapping = {
            'sched_boundary/sched_boundary.py': './',
            'sched_boundary/process.py': './',
            'sched_boundary/sched_boundary.yaml': './',
            'src/*.[ch]': 'kernel/sched/mod',
            'src/Makefile': 'kernel/sched/mod/',
            'src/plugsched.lds': 'kernel/sched/mod/',
            'src/Makefile.plugsched': './'
        }
        self.threads = threads
        self.mod_files = self.config['mod_files']
        self.mod_srcs = [f for f in self.mod_files if f.endswith('.c')]
        self.mod_hdrs = [f for f in self.mod_files if f.endswith('.h')]
        self.mod_objs = [f[:-2]+'.o' for f in self.mod_srcs]
        self.extracted_mod_srcs = [os.path.join('kernel/sched/mod', os.path.basename(f)) for f in self.mod_srcs]
        self.extracted_mod_files = self.extracted_mod_srcs + self.mod_hdrs
        self.sanity_checker = SanityChecker(self.mod_path, self.kernel_path)

    def apply_patch(self, f, **kwargs):
        self.mod_sh.patch(input=os.path.join(self.plugsched_path, 'src', f), strip=1, **kwargs)

    def make(self, objs=[], **kwargs):
        self.mod_sh.make('sched_mod',
                         'AR="echo"',
                         objs,
                         *['%s=%s' % i for i in kwargs.items()],
                         file='Makefile.plugsched',
                         jobs=self.threads)

    def fix_up(self):
        self.mod_sh.sed("s/#include \"/#include \"..\//g;"  + \
                        "/EXPORT_SYMBOL/d;"                 + \
                        "/initcall/d;"                      + \
                        "/early_param/d;"                   + \
                        "/\<__init\>/d;"                    + \
                        "/\<__initdata\>/d;"                + \
                        "/__setup/d;"                       + \
                        "s/struct atomic_t /atomic_t /g",
                        self.extracted_mod_srcs,
                        in_place=True)

    def extract(self, system_map):
        if not self.sanity_checker.check('extract'):
            logging.fatal('Sanity checking faild before extracting')
        logging.info('Extracting scheduler module objs: %s', ' '.join(self.mod_objs))
        self.make(SCHED_MOD_STAGE = 'collect')
        self.make(SCHED_MOD_STAGE = 'analyze',
                  SYSTEM_MAP      = system_map)
        self.make(SCHED_MOD_STAGE = 'extract',
                  objs            = self.mod_objs)
        with open(os.path.join(self.mod_path, 'kernel/sched/mod/export_jump.h'), 'a') as f:
            sh.sort(glob('kernel/sched/*.fn_ptr.h', _cwd=self.mod_path), _out=f)

    def create_mod(self, kernel_config):
        logging.info('Creating mod build directory structure')
        rsync(self.kernel_path + '/', self.mod_path + '/', archive=True, verbose=True, delete=True, exclude=".git")
        self.mod_sh.mkdir('kernel/sched/mod', parents=True)

        for f, t in self.file_mapping.items():
            self.mod_sh.cp(glob(f, _cwd=self.plugsched_path), t)

        if kernel_config:
            self.mod_sh.cp(kernel_config, '.config', force=True)
        else:
            arch = sh.grep(sh.lscpu(), 'Architecture:').split()[1]
            self.mod_sh.cp('configs/config-4.19.y-' + arch, '.config', force=True)

        logging.info('Patching kernel kbuild system')
        self.apply_patch('kbuild.patch')

    def cmd_init(self, system_map, kernel_config='', kernel_customized=[]):
        if not os.path.exists(kernel_config):
            logging.fatal("Kernel config not specified")
        self.create_mod(kernel_config)
        # precompile some files to avoid ugly building trouble
        self.mod_sh.make(
            'scripts/mod/',
            'arch/x86/platform/',
            'arch/x86/purgatory/',
            'arch/x86/realmode/rm/',
            'arch/x86/entry/vdso/',
            'arch/x86/lib/',
            'arch/x86/oprofile/',
            jobs=self.threads
        )
        if 'task_life_hook' not in kernel_customized:
            self.mod_sh.sed('/EXPORT_PLUGSCHED(release_task_reserve/d', 'kernel/sched/mod/export_jump.h', in_place=True)
            self.mod_sh.sed('/EXPORT_PLUGSCHED(init_task_reserve/d', 'kernel/sched/mod/export_jump.h', in_place=True)
        self.extract(system_map)
        with open(os.path.join(self.mod_path, '.gitignore'), 'a') as f:
            f.write('*.sched_boundary\n*.fn_ptr.h')
        if not self.sanity_checker.check('after_extract'):
            logging.fatal('Sanity checking faild after extracting')
        logging.info('Fixing up extracted scheduler module')
        self.fix_up()
        logging.info('Patching extracted scheduler module')
        self.apply_patch('module.patch')
        if 'builtin_springboard' not in kernel_customized:
            self.apply_patch('dynamic_springboard.patch')
        try:
            springboard = self.search_springboard('vmlinux')

            if len(list(springboard)) != 1:
                logging.error("Search springboard faild!")
                exit(-1)

            with open(os.path.join(self.mod_path, 'kernel/sched/mod/Makefile'), 'a') as f:
                f.write('ccflags-y += -DSPRINGBOARD=' + str(springboard))

        except sh.ErrorReturnCode:
            logging.error("Search springboard faild!")
            exit(-1)

        logging.info("Succeed!")

    def cmd_build(self):
        if not self.sanity_checker.check('build'):
            logging.fatal('Sanity checking faild before build')
        if not os.path.exists(self.mod_path):
            logging.fatal("plugsched: Can't find %s", self.mod_path)
        logging.info("Preparing rpmbuild environment")
        rpmbuild_root = os.path.join(self.plugsched_path, 'rpmbuild')
        self.plugsched_sh.rm('rpmbuild', recursive=True, force=True)
        self.plugsched_sh.mkdir('rpmbuild')
        rpmbase_sh = sh(_cwd=rpmbuild_root)
        rpmbase_sh.mkdir(['BUILD','RPMS','SOURCES','SPECS','SRPMS'])
        VERSION = self.kernel_sh.awk('-F=', '/^VERSION/{print $2}', 'Makefile').strip()
        PATCHLEVEL = self.kernel_sh.awk('-F=', '/^PATCHLEVEL/{print $2}', 'Makefile').strip()
        SUBLEVEL = self.kernel_sh.awk('-F=', '/^SUBLEVEL/{print $2}', 'Makefile').strip()
        KVER = '%s.%s.%s' % (VERSION, PATCHLEVEL, SUBLEVEL)
        rpmname = 'plugsched-{}'.format(KVER)
        self.mod_sh.cp(os.path.join(self.kernel_devel_path, 'Module.symvers'), '.')

        self.plugsched_sh.cp('plugsched.spec', os.path.join(rpmbuild_root, 'SPECS'), force=True)
        rpmbase_sh.rpmbuild('--define', '%%_outdir %s' % os.path.realpath(self.plugsched_path),
                            '--define', '%%_topdir %s' % os.path.realpath(rpmbuild_root),
                            '--define', '%%_dependdir %s' % os.path.realpath(self.plugsched_path),
                            '--define', '%%_kerneldir %s' % os.path.realpath(self.mod_path),
                            '--define', '%%KVER %s' % KVER,
                            '--define', '%%name %s' % rpmname,
                            '--define', '%%threads %d' % self.threads,
                            '-bb', 'SPECS/plugsched.spec')
        #check_sym_duplicy.run_test(self.mod_path, self.kernel_debuginfo_path)
        logging.info("Succeed!")

class PlugschedCLI(object):
    """ A command line interface for plugsched """

    def dep(self, j=1):
        """ Building dependencies (gcc-python-plugin)

        :param j: Number of threads. "-j N" is okay while "-jN" is not allowed.
        """
        root_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)))
        plugsched_sh = sh(_cwd = root_dir)
        plugsched_sh.git.submodule.update(init = True)
        depsh = sh(_cwd = os.path.join(root_dir, 'gcc-python-plugin'))
        with sh.contrib.sudo:
            sh.yum.install('python-devel', 'gcc', 'gcc-plugin-devel', _fg=True)
        depsh.make(jobs=j)

    def init(self, kernel_path, mod_path, kernel_debuginfo_path, j=1, kernel_config='',
			system_map='', kernel_customized=''):
        """ Initialize a scheduler module for a specific kernel release and product

        :param j: Number of threads. "-j N" is okay while "-jN" is not allowed.
        :param kernel_config: Specify kernel_config to create scheduler module
        :param kernel_customized: builtin_springboard | task_life_hook
        """
        self.plugsched = Plugsched(kernel_path, mod_path, threads=j, kernel_debuginfo_path=kernel_debuginfo_path)
        self.plugsched.cmd_init(system_map, kernel_config, kernel_customized.split('|'))

    def build(self, kernel_path, mod_path, kernel_devel_path, kernel_debuginfo_path, j=1):
        """ Build a scheduler module rpm package for a specific kernel release and product

        :param product: The name of the product, eg. odps/fc/drds/ecs
        :param j: Number of threads. "-j N" is okay while "-jN" is not allowed.
        """
        self.plugsched = Plugsched(kernel_path, mod_path, threads=j, kernel_debuginfo_path=kernel_debuginfo_path, kernel_devel_path=kernel_devel_path)
        self.plugsched.cmd_build()

    def self_debug(self, func, *args, **kwargs):
        """ Debug plugsched tool itself

        :param func: The process of plugsched to be debugged
        :param args: Any arguments to be passed to func
        :param kwargs: Any positional arguments to be passed to func
        """
        self.plugsched = Plugsched(*args, **kwargs)
        getattr(self.plugsched, func)(*args, **kwargs)

if __name__ == '__main__':
    fire.Fire(PlugschedCLI)
