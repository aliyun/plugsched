#!/bin/bash
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

vmlinux=$1

if [ ! -f $vmlinux ]; then
	exit -1
fi

startaddress=$(nm -n $vmlinux | awk '$NF == "__schedule"{print "0x"$1;exit}')
endaddress=$(nm -n $vmlinux | awk '$NF == "__schedule"{getline; print "0x"$1;exit}')

if [ $startaddress == $endaddress ]; then
	exit -1
fi

target_addr=$(objdump -d $vmlinux --start-address=$startaddress --stop-address=$endaddress | \
	awk '$NF == "<__switch_to_asm>"{getline; print $1; exit}')
target_addr=0x${target_addr%:*}

stack_size=$(objdump -d $vmlinux --start-address=$startaddress --stop-address=$endaddress | \
	head -n 20 | grep sub | grep rsp | awk 'NR==1{print $NF}')

stack_size=${stack_size%,*}

echo $((target_addr-startaddress))
echo ${stack_size#*$}
