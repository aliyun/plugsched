#!/usr/bin/env python2
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

from yaml import load, dump
from yaml import CLoader as Loader, CDumper as Dumper
import coloredlogs
import logging
import sys
coloredlogs.install(level='INFO')

def YamlDiff(old_file, new_file):

    """ find the difference of two sched_boundary_extract.yaml

    :param old_file: the 1st yaml file
    :param new_file: the 2nd yaml file
    """

    with open(old_file) as f:
        old_yaml = load(f, Loader)

    with open(new_file) as f:
        new_yaml = load(f, Loader)

    old_set = set(old_yaml['function']['outsider'])
    new_set = set(new_yaml['function']['outsider'])

    for changed in (old_set | new_set) - (old_set & new_set):
        logging.warn('DIFF: check the outsider \"%s\"', changed)

    logging.info("Bye: analyze the DIFF and remember to update sched_boundary.yaml")

if __name__ == '__main__':
    YamlDiff(sys.argv[1], sys.argv[2])
