#!/bin/bash
# Copyright 2019-2022 Alibaba Group Holding Limited.
# SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause

function get_function_range()
{
        addr_size=$(nm -S $object | grep " $1\$" | awk '{print "0x"$1,"0x"$2}')
        read -r start_addr size <<< "$addr_size"

        if [ "$start_addr" == "" ]; then
		1>&2 echo "ERROR: __schedule function range not found in target object"
                exit 1
        fi

        end_addr=$(python3 -c "print(hex($start_addr + $size))")
        echo "start_addr=$start_addr; end_addr=$end_addr"
}

function get_function_asm()
{
	if [ "$stage" == "init" ]; then
		objdump -d $object --start-address=$start_addr --stop-address=$end_addr | sed -e '/ <.*>:$/d;/^$/d'
	else
		objdump -d $object | grep "<__schedule>:" -A30
	fi
}

function get_stack_size_x86_64()
{
	stack_size=$(awk '/sub.*,%rsp/ {print $NF; exit}' <<< "$schedule_asm")
	stack_size=${stack_size%,*}
	stack_size=${stack_size#*$}

	if [ -z "${stack_size// }" ]; then
		1>&2 echo "ERROR: stack_size of __schedule not found in target object."
		exit 1
	fi
	echo $stack_size
}

function get_stack_size_aarch64()
{
	stack_size=$(awk '/stp\s*x29, x30/{print $NF; quit}' <<< "$schedule_asm")
	stack_size=${stack_size%]*}
	stack_size=${stack_size#*-}

	if [ -z "${stack_size// }" ]; then
		1>&2 echo "ERROR: stack_size of __schedule not found in target object."
		exit 1
	fi
	echo $stack_size
}

function get_springboard_target()
{
	target_addr=$(awk '$NF == "<'$1'>"{print $1; exit}' <<< "$schedule_asm")
	target_addr=0x${target_addr%:*}
	target_off=$((target_addr-start_addr))

	if   [ -z "${target_off// }" ]; then
		1>&2 echo "ERROR: springboard not found in target object."
		exit 1
	fi
	echo $target_off
}

function get_springboard_target_x86_64()
{
	get_springboard_target __switch_to_asm
}

function get_springboard_target_aarch64()
{
	get_springboard_target __switch_to
}

function get_stack_check_off_x86_64() { :; }

function get_stack_check_off_aarch64()
{
	stack_chk_fail=$(awk '$3 == "bl" && $NF=="<__stack_chk_fail>"{print "0x"$1}' <<< "$schedule_asm")
	stack_chk_fail=${stack_chk_fail%:*}
	stack_chk_fail_off=$(printf "0x%x" $((stack_chk_fail-start_addr)))
	stack_chk_fail_off_by_4=$(printf "0x%x" $((stack_chk_fail-start_addr-4)))

	asm_sequence=$(awk '
		/Disassembly of section/ {start = 1; next}
		start == 1 && $3 == "ldr" {print "ldr"; next}
		start == 1 && $3 == "ldp" {print "ldp"; next}
		start == 1 && $3 == "ret" {print "ret"; next}
		start == 1 && $5 == "<__schedule+'$stack_chk_fail_off'>" {print "chk"; next}
		start == 1 && $6 == "<__schedule+'$stack_chk_fail_off'>" {print "chk"; next}
		start == 1 && $5 == "<__schedule+'$stack_chk_fail_off_by_4'>" {print "chk"; next}
		start == 1 && $6 == "<__schedule+'$stack_chk_fail_off_by_4'>" {print "chk"; next}
		start == 1 {print "any"}' <<< "$schedule_asm")


	stack_chk_seq_with_off=$(echo $asm_sequence | grep -Po 'ldr ldr (any ){1,4}chk (ldp ){5,6}ret' --byte-offset)
	stack_chk_off=$(cut -d: -f1 <<< "$stack_chk_seq_with_off")
	stack_chk_seq=$(cut -d: -f2 <<< "$stack_chk_seq_with_off")
        # Sequence length without ldp * {5,6} + ret
        stack_chk_len=$(echo "${stack_chk_seq}" | sed 's/chk.*/chk/g'  | awk '{print NF}')

	if [ -z "${stack_chk_off}" ] || [ ${stack_chk_off} -eq 0 ]; then
		>&2 echo 'ERROR: Stack protector sequence  "ldr ldr (any ){1,4}chk (ldp ){6}ret" not found:'
		>&2 echo "$asm_sequence"
		exit 1
	fi

	echo "stack_chk_off=$stack_chk_off; stack_chk_len=$stack_chk_len"
}

function get_stack_layout_x86_64()
{
	echo $schedule_asm | awk '{for(i = 0; i <= NF; i++) if($i == "push") {print $(i+1);break;}}' | hexdump -ve '"%x"'
}

function get_stack_layout_aarch64()
{
	echo $schedule_asm | awk '{for(i = 0; i <= NF; i++) if($i == "stp") {print $(i+1);break;}}' | hexdump -ve '"%x"'
}

function output()
{
	echo "ccflags-y += -DSPRINGBOARD=$target_off"
	echo "ccflags-y += -DSTACKSIZE_VMLINUX=$stack_size"
	if [ $flag_stack_protector = "Y" ]; then
		echo "ccflags-y += -DSTACK_PROTECTOR=$stack_chk_off"
		echo "ccflags-y += -DSTACK_PROTECTOR_LEN=$stack_chk_len"
	fi
	echo "ccflags-y += -DVMLINUX_FRAME_POINTER=0x$(get_stack_layout_$arch)"
}

function read_config()
{
	if grep -q CONFIG_STACKPROTECTOR=y $config; then
		flag_stack_protector=Y
	else
		flag_stack_protector=N
	fi
}

function do_search()
{
	read_config
	eval $(get_function_range __schedule)
	schedule_asm="$(get_function_asm)"
	target_off=$(get_springboard_target_$arch)
	stack_size=$(get_stack_size_$arch)
	if [ $flag_stack_protector = "Y" ]; then
		eval $(get_stack_check_off_$arch)
	fi
	output
}


stage=$1
object=$2
config=$3

arch=$(arch)

if [ "$stage" == "init" ]; then
	do_search
elif [ "$stage" == "build" ]; then
	schedule_asm="$(get_function_asm)"
	size=$(get_stack_size_$arch)
	stack_layout=0x$(get_stack_layout_$arch)
	echo "-DSTACKSIZE_MOD=$size -DMODULE_FRAME_POINTER=$stack_layout"
else
	1>&2 echo "Usage: springboard_search.sh <stage> <object>."
	exit 1
fi
