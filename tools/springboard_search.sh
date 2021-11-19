#!/bin/bash

vmlinux=$1

if [ ! -f $vmlinux ]; then
	exit -1
fi

startaddress=$(nm -n $vmlinux | awk '$NF == "__schedule"{print "0x"$1;exit}')
endaddress=$(nm -n $vmlinux | awk '$NF == "__schedule"{getline; print "0x"$1;exit}')

if [ $startaddress -eq $endaddress ]; then
	exit -1
fi

target_addr=$(objdump -d $vmlinux --start-address=$startaddress --stop-address=$endaddress | \
	awk '$NF == "<__switch_to_asm>"{getline; print $1; exit}')
target_addr=0x${target_addr%:*}

echo $((target_addr-startaddress))
