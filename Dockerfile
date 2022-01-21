# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
From openanolis/anolisos:8.4-x86_64

RUN yum install python2 gcc gcc-c++ wget -y && \
    wget https://bootstrap.pypa.io/pip/2.7/get-pip.py && \
    python2 get-pip.py
RUN pip install --upgrade setuptools && \
    pip install six pyyaml sh coloredlogs future fire jinja2 docopt && \
    yum install make bison flex \
		gcc-plugin-devel.x86_64 python2-devel \
		elfutils-libelf-devel.x86_64 openssl openssl-devel \
		elfutils-devel-static \
		glibc-static zlib-static \
		libstdc++-static \
		gcc-python-plugin \
    		rpm-build rsync bc perl -y && \
    yum clean all

COPY . /usr/local/lib/plugsched/
RUN ln -s /usr/local/lib/plugsched/cli.py /usr/local/bin/plugsched-cli
