/*
 * Common module linker script, always used when linking a module.
 * Archs are free to supply their own linker scripts.  ld will
 * combine them automatically.
 */
SECTIONS {
	PROVIDE(__mod_sched_start = .);
	.sched.text		0 : ALIGN(8) { *(.sched.text) }
	PROVIDE(__mod_sched_end = .);
}
