/*
 * Common module linker script, always used when linking a module.
 * Archs are free to supply their own linker scripts.  ld will
 * combine them automatically.
 *
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */
SECTIONS {
	PROVIDE(__module_sched_start = .);
	.sched.text		0 : ALIGN(8) { *(.sched.text) }
	PROVIDE(__module_sched_end = .);
}
