# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
From openanolis/anolisos:8.4-x86_64

RUN yum install python2 python2-devel gcc gcc-c++ wget libyaml-devel -y && \
    wget https://bootstrap.pypa.io/pip/2.7/get-pip.py && \
    python2 get-pip.py
RUN pip install --upgrade setuptools && \
    pip install --global-option='--with-libyaml' pyyaml && \
    pip install six sh coloredlogs future fire jinja2 docopt && \
    yum install make bison flex \
		gcc-plugin-devel.x86_64 \
		elfutils-libelf-devel.x86_64 openssl openssl-devel \
		elfutils-devel-static \
		glibc-static zlib-static \
		libstdc++-static \
    		rpm-build rsync bc perl -y && \
    yum install gcc-python-plugin --enablerepo=Plus -y && \
    yum clean all

COPY . /usr/local/lib/plugsched/
RUN ln -s /usr/local/lib/plugsched/cli.py /usr/local/bin/plugsched-cli
