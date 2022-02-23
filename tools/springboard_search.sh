#!/bin/bash
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

function get_function_range()
{
        addrs=$(nm -n $vmlinux | grep " $1\$" -A1 | awk '{printf " 0x"$1}')
        read -r start_addr end_addr <<< "$addrs"

        if [ $start_addr == $end_addr ]; then
		1>&2 echo "ERROR: __schedule function range not found in vmlinux"
                exit 1
        fi

        echo "start_addr=$start_addr; end_addr=$end_addr"
}

function get_function_asm()
{
	objdump -d $vmlinux --start-address=$start_addr --stop-address=$end_addr
}

function get_stack_size()
{
	stack_size=$(awk '/sub.*,%rsp/ {print $NF; exit}' <<< "$schedule_asm")
	stack_size=${stack_size%,*}
	stack_size=${stack_size#*$}

	if [ -z "${stack_size// }" ]; then
		1>&2 echo "ERROR: stack_size of __schedule not found in vmlinux."
		exit 1
	fi
	echo $stack_size
}

function get_springboard_target()
{
	target_addr=$(awk '$NF == "<__switch_to_asm>"{getline; print $1; exit}' <<< "$schedule_asm")
	target_addr=0x${target_addr%:*}
	target_off=$((target_addr-start_addr))

	if   [ -z "${target_off// }" ]; then
		1>&2 echo "ERROR: springboard not found in vmlinux."
		exit 1
	fi
	echo $target_off
}

function output()
{
	echo "ccflags-y += -DSPRINGBOARD=$target_off"
	echo "ccflags-y += -DSTACKSIZE_SCHEDULE=$stack_size"
}

function do_search()
{
        eval $(get_function_range __schedule)
	schedule_asm="$(get_function_asm)"
	target_off=$(get_springboard_target)
	stack_size=$(get_stack_size)

	output
}


vmlinux=$1
if [ ! -f $vmlinux ]; then
	1>&2 echo "Usage: springboard_search.sh <vmlinux>."
	exit 1
fi
do_search
