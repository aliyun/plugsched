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

function get_stack_size_X86_64()
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

function get_stack_size_AArch64()
{
	stack_size=$(awk '/stp\s*x29, x30/{print $NF; quit}' <<< "$schedule_asm")
	stack_size=${stack_size%]*}
	stack_size=${stack_size#*-}

	if [ -z "${stack_size// }" ]; then
		1>&2 echo "ERROR: stack_size of __schedule not found in vmlinux."
		exit 1
	fi
	echo $stack_size
}

function get_springboard_target()
{
	target_addr=$(awk '$NF == "<'$1'>"{getline; print $1; exit}' <<< "$schedule_asm")
	target_addr=0x${target_addr%:*}
	target_off=$((target_addr-start_addr))

	if   [ -z "${target_off// }" ]; then
		1>&2 echo "ERROR: springboard not found in vmlinux."
		exit 1
	fi
	echo $target_off
}

function get_springboard_target_X86_64()
{
	get_springboard_target __switch_to_asm
}

function get_springboard_target_AArch64()
{
	get_springboard_target __switch_to
}

function get_stack_check_off_X86_64() { :; }

function get_stack_check_off_AArch64()
{
	stack_chk_fail=$(awk '$3 == "bl" && $NF=="<__stack_chk_fail>"{print "0x"$1}' <<< "$schedule_asm")
	stack_chk_fail=${stack_chk_fail%:*}
	stack_chk_fail_off=$(printf "0x%x" $((stack_chk_fail-start_addr)))

	asm_sequence=$(awk '
		$NF == "<__schedule>:" {start = 1; next}
		start == 1 && $3 == "ldr" {print "ldr"; next}
		start == 1 && $3 == "ldp" {print "ldp"; next}
		start == 1 && $3 == "ret" {print "ret"; next}
		start == 1 && $NF== "<__schedule+'$stack_chk_fail_off'>" {print "chk"; next}
		start == 1 {print "any"}' <<< "$schedule_asm")


	stack_chk_seq_with_off=$(echo $asm_sequence | grep -Po 'ldr ldr (any ){1,4}chk (ldp ){6}ret' --byte-offset)
	stack_chk_off=$(cut -d: -f1 <<< "$stack_chk_seq_with_off")
	stack_chk_seq=$(cut -d: -f2 <<< "$stack_chk_seq_with_off")
	stack_chk_len=$(awk '{print NF - 7}' <<< "${stack_chk_seq}") # Sequence length without ldp * 6 + ret

	if [ ${stack_chk_off} -eq 0 ]; then
		>&2 echo 'ERROR: Stack protector sequence  "ldr ldr (any ){1,4}chk (ldp ){6}ret" not found:'
		>&2 echo "$asm_sequence"
		exit 1
	fi

	echo "stack_chk_off=$stack_chk_off; stack_chk_len=$stack_chk_len"
}

function output()
{
	echo "ccflags-y += -DSPRINGBOARD=$target_off"
	echo "ccflags-y += -DSTACKSIZE_SCHEDULE=$stack_size"
	if [ $flag_stack_protector = "Y" ]; then
		echo "ccflags-y += -DSTACK_PROTECTOR=$stack_chk_off"
		echo "ccflags-y += -DSTACK_PROTECTOR_LEN=$stack_chk_len"
	fi
}

function read_config()
{
	if grep -q CONFIG_STACKPROTECTOR_PER_TASK=y $config; then
		flag_stack_protector=Y
	else
		flag_stack_protector=N
	fi
}

function do_search()
{
	read_config
	arch=$(readelf -h $vmlinux | awk '/Machine:/{print $NF}' | tr '-' '_')
        eval $(get_function_range __schedule)
	schedule_asm="$(get_function_asm)"
	target_off=$(get_springboard_target_$arch)
	stack_size=$(get_stack_size_$arch)
	if [ $flag_stack_protector = "Y" ]; then
		eval $(get_stack_check_off_$arch)
	fi
	output
}


vmlinux=$1
config=$2
if [ ! -f $vmlinux ]; then
	1>&2 echo "Usage: springboard_search.sh <vmlinux>."
	exit 1
fi
do_search
