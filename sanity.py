"""
Checks the sanity when plugsched is in development
"""

import sys
from glob import glob
import os
from builtins import super
import sh
import yaml
from yaml import load, dump
from itertools import chain as _chain
chain = _chain.from_iterable
from yaml import CLoader as Loader, CDumper as Dumper
import coloredlogs
import logging
coloredlogs.install(level='INFO')

# Nothing needs to be updated
SanityLevelSane = 0
# Tests should be rerun
SanityLevelTests = 1
# Base should be updated (but don't need to extract again)
SanityLevelBaseInsane = 2
# Base should be updated (cli.py init)
SanityLevelBoundaryxInsane = 3
# Yaml should be updated (vim sched_boundary/sched_boundary.yaml)
SanityLevelYamlInsane = 4

path = os.path.dirname(os.path.abspath(__file__))
plugsched_sh = sh(_cwd=path)

class Version(object):
    def __init__(self, stage):
        self.stage = stage
        self.version = None

    def get_version(self):
        if self.version is not None:
            return self.version
        version = self._get_version()
        # Make it hashable
        if isinstance(version, list):
            version = tuple(version)
        self.version = version
        return self.version

    def advise(self):
        version_file_revision = plugsched_sh.git('rev-list', '--max-count=1', 'HEAD', '--', 'versions').strip()
        logging.warning("versions file hasn't been updated since commit %s", version_file_revision)

class ExtractorVersion(Version):
    def __init__(self):
        super().__init__(SanityChecker.STAGE_SINCE_FORK)
        self.sched_boundary_files = [
            'src/export_jump.h',
            'src/kbuild.patch',
            'sched_boundary',
            'tests/check_module_vmlinux.py',
            'tests/check_module_undef.py',
            'cli.py'
        ]
        self.base_files = list(set(glob('src/*')) - {'src/export_jump.h'})

    def _get_version(self):
        return str(sh.head(plugsched_sh.git('rev-list', 'HEAD', '--', self.sched_boundary_files, self.base_files, _piped=True), '-1').strip())

    def advise(self, old_ver):
        super().advise()
        logging.warning("Re-run cli.py init (If you haven't.) Update versions.")

class FunctionBoundaryVersion(Version):
    def __init__(self, mod_path, kernel_path):
        super().__init__(SanityChecker.STAGE_AFTER_EXTRACT)
        self.config = os.path.join(mod_path, 'sched_boundary_extract.yaml')
        self.kernel_path = kernel_path

    def _get_version(self):
        with open(self.config) as f:
            yaml = load(f, Loader)
        return sorted(map(str, sh.git('ls-files', 'kernel/sched/', _cwd=self.kernel_path).split())) + \
               sorted(yaml['function']['outsider'])

    def advise(self, old_ver):
        super().advise()
        nv = set(self.version)
        ov = set(old_ver)
        for changed in (nv | ov) - (nv & ov):
            if '/' in changed:
                logging.warn('Check the file %s, update sched_boundary.yaml.', changed)
            else:
                logging.warn('Check the outsider %s, update sched_boundary.yaml.', changed)
        logging.warning('Re-run cli.py init. Update versions')

class StructureBoundaryVersion(Version):
    def __init__(self, mod_path):
        super().__init__(SanityChecker.STAGE_AFTER_EXTRACT)
        self.config = os.path.join(mod_path, 'sched_boundary_doc.yaml')

    def _get_version(self):
        with open(self.config) as f:
            yaml = load(f, Loader)
        return sorted(chain([(struct, field) for field in fields['public_fields']]
                                             for struct, fields in yaml.iteritems()))

    def advise(self, old_ver):
        super().advise()
        nv = set(self.version)
        ov = set(old_ver)
        for struct, field in (nv | ov) - (nv & ov):
            logging.warning('Review code to check the semantic of %s.%s, update versions', struct, field)
        if not set(self.version) - set(old_ver):
            logging.warning('Nothing needs to be done. Just updating versions is enough.')

class CurrentVersion(Version):
    def __init__(self):
        super().__init__(SanityChecker.STAGE_SINCE_EXTRACT)
        self.config = 'versions'

    def _get_version(self):
        with open(self.config) as f:
            return load(f, Loader)

    def update(self, new_ver):
        with open(self.config, 'w') as f:
            dump(new_ver, f, Dumper)

class SanityChecker(object):
    STAGE_SINCE_EXTRACT = {'extract', 'fork', 'build'}
    STAGE_AFTER_EXTRACT = {'after_extract', 'fork', 'build'}
    STAGE_SINCE_FORK    = {'fork', 'build'}

    def __init__(self, mod_path, kernel_path):
        self.mod_path = mod_path
        self.available_version = [
            ExtractorVersion(),
            FunctionBoundaryVersion(mod_path, kernel_path),
            StructureBoundaryVersion(mod_path),
        ]
        self.current_version = CurrentVersion()

    def check(self, current_stage):
        current_version = self.current_version.get_version()
        fail = False
        for version in self.available_version:
            if current_stage in version.stage:
                n = type(version).__name__
                v = version.get_version()
                cv = current_version[n]
                if cv != v:
                    logging.error("Version not match on %s", n)
                    version.advise(cv)
                    fail = True
        return not fail

    def get_versions(self):
        versions = {}
        for version in self.available_version:
            n = type(version).__name__
            v = version.get_version()
            versions[n] = v
        return versions

class cli(object):
    def dry(self, mod_path, kernel_path):
        sanity_checker = SanityChecker(mod_path, kernel_path)
        v = sanity_checker.get_versions()
        print dump(v)

    def check_all(self, mod_path, kernel_path):
        sanity_checker = SanityChecker(mod_path, kernel_path)
        # Check the last stage is enough
        sanity_checker.check('build')

    def update(self, mod_path, kernel_path):
        sanity_checker = SanityChecker(mod_path, kernel_path)
        versions = sanity_checker.get_versions()
        current_versions = sanity_checker.current_version.get_version()
        assert set(versions.keys()) == set(current_versions.keys())
        for k, v in current_versions.iteritems():
            if v != versions[k]:
                logging.info("Please ensure you have done related work to update .base accordingly")
                answer = raw_input("Update version of {} to {}? (y/n): ".format(k, versions[k])).strip()
                if answer in ('y', 'Y'):
                    current_versions[k] = versions[k]
        sanity_checker.current_version.update(current_versions)

if __name__ == '__main__':
    import fire
    logging.warning("This checker will be integrated into cli.py later. For now, run it from time to time")
    fire.Fire(cli)
