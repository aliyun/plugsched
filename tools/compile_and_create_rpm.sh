#!/bin/bash
# Copyright 2019-2023 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
# This script will compile plugsched and create a rpm package
# test on Anolis 7.9 AHCK
# examples/rpm_test_example.diff is the example patch_file

patch_file=""
if [ $# == 1 ]; then
    if [ ${1} = "-h" -o ${1} = "--help" ]; then
        echo "usage: ${0} patch_file"
        exit 0
    fi
    patch_file=${1}
    if [ ! -f $patch_file ]; then
        echo "$patch_file is not a file"
        exit 1
    fi
fi

yum install anolis-repos -y
yum install yum-utils podman -y
yum install kernel-debuginfo-$(uname -r) kernel-devel-$(uname -r) --enablerepo=Plus-debuginfo --enablerepo=Plus -y

mkdir -p /tmp/work
if [ ! -z $patch_file ]; then
    /bin/cp -f $patch_file /tmp/work/test.diff
    patch_cmd="patch -p1 -f <test.diff"
else
    patch_cmd=""
fi
cd /tmp/work
yumdownloader --source kernel-$(uname -r) --enablerepo=Plus --enablerepo=Plus-debuginfo

(podman images | grep docker.io/plugsched/plugsched-sdk) || podman pull docker.io/plugsched/plugsched-sdk
(podman ps -a | grep plugsched) && podman stop plugsched && podman rm plugsched
podman run -itd --name=plugsched -v /tmp/work:/tmp/work -v /usr/src/kernels:/usr/src/kernels -v /usr/lib/debug/lib/modules:/usr/lib/debug/lib/modules docker.io/plugsched/plugsched-sdk

cat >/tmp/work/create_rpm.sh <<EOF
cd /tmp/work
uname_r=\$(uname -r)
plugsched-cli extract_src kernel-\${uname_r%.*}.src.rpm ./kernel
plugsched-cli init $(uname -r) ./kernel ./scheduler
${patch_cmd}
plugsched-cli build /tmp/work/scheduler
cp /usr/local/lib/plugsched/rpmbuild/RPMS/x86_64/scheduler-xxx-\${uname_r%.*}.yyy.x86_64.rpm /tmp/work/scheduler-xxx.rpm
EOF

podman exec plugsched bash /tmp/work/create_rpm.sh

if [ ! -f /tmp/work/scheduler-xxx.rpm ]; then
    echo "create rpm failed"
    exit 1
else
    echo "create rpm success: /tmp/work/scheduler-xxx.rpm"
fi


