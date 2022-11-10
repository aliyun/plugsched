// Copyright 2019-2022 Alibaba Group Holding Limited.
// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause


/*
 * helper function to communicate with vmlinux
 */

#ifdef CONFIG_X86_64
static unsigned long orig_cr0;

static inline void do_write_cr0(unsigned long val)
{
	asm volatile("mov %0,%%cr0": "+r" (val) : : "memory");
}

static inline void *disable_write_protect(void *addr)
{
	BUG_ON(orig_cr0);

	orig_cr0 = read_cr0();
	do_write_cr0(orig_cr0 & 0xfffeffff);

	return (void *)addr;
}

static inline void enable_write_protect(void)
{
	do_write_cr0(orig_cr0);
	orig_cr0 = 0;
}

#else /* ARM64 */

#include <asm/insn.h>
#include <asm/fixmap.h>
#include <asm/memory.h>
#include <asm/cacheflush.h>

static void *disable_write_protect(void *addr)
{
	unsigned long uintaddr = (uintptr_t) addr;
	struct page *page;

	page = phys_to_page(__pa_symbol(addr));

	return (void *)set_fixmap_offset(FIX_TEXT_POKE0, page_to_phys(page) +
			(uintaddr & ~PAGE_MASK));
}

static inline void enable_write_protect(void)
{
	clear_fixmap(FIX_TEXT_POKE0);
}
#endif


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
static int bsearch(unsigned long *arr, int start, int end, unsigned long tar)
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
static void addr_sort(unsigned long *addr, unsigned long *size, int n) {
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
