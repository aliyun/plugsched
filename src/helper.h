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

static inline void addr_swap(unsigned long *a, unsigned long *b)
{
	if (*a ^ *b) {
		*a = *a ^ *b;
		*b = *b ^ *a;
		*a = *a ^ *b;
	}
}

/*
 * This sort method is coming from lib/sort.c
 */
void addr_sort(unsigned long *addr, unsigned long *size, int n) {
	int i = n/2 - 1, c, r;

	for ( ; i >= 0; i -= 1) {
		for (r = i; r * 2 + 1 < n; r  = c) {
			c = r * 2 + 1;
			if (c < n - 1 &&
					*(addr + c) < *(addr + c + 1))
				c += 1;
			if (*(addr + r) >= *(addr + c))
				break;
			addr_swap(addr + r, addr + c);
			addr_swap(size + r, size + c);
		}
	}

	for (i = n - 1; i > 0; i -= 1) {
		addr_swap(addr, addr + i);
		addr_swap(size, size + i);
		for (r = 0; r * 2 + 1 < i; r = c) {
			c = r * 2 + 1;
			if (c < i - 1 &&
					*(addr + c) < *(addr + c + 1))
				c += 1;
			if (*(addr + r) >= *(addr + c))
				break;
			addr_swap(addr + r, addr + c);
			addr_swap(size + r, size + c);
		}
	}
}
