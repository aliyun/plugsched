// Copyright 2019-2022 Alibaba Group Holding Limited.
// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause


/*
 * helper function to communicate with vmlinux
 */

static inline unsigned long get_ptr_value(unsigned long ptr_addr)
{
	unsigned long mid_addr = *((unsigned long *)ptr_addr);
	return *((unsigned long *)mid_addr);
}

static inline void set_ptr_value(unsigned long ptr_addr, unsigned long val)
{
	unsigned long mid_addr = *((unsigned long *)ptr_addr);
	*((unsigned long *)mid_addr) = val;
}

static inline unsigned long get_value_long(unsigned long addr)
{
	return *((unsigned long *)addr);
}

static inline void set_value_long(unsigned long addr, unsigned long val)
{
	*((unsigned long *)addr) = val;
}

/*
 * binary search method
 */

int bsearch(unsigned long *arr, int start, int end, unsigned long tar)
{
	int mid;

	if (end < start)
		return -1;
	if (tar < arr[start])
		return -1;
	if (tar >= arr[end])
		return end;

	while(start <= end) {
		mid = (start + end) >> 1;
		if (tar == arr[mid])
			return mid;
		else if (tar < arr[mid])
			end = mid - 1;
		else
			start = mid + 1;
	}

	return end;
}
