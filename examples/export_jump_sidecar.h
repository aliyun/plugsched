/*
 * There are often cases when we want to patch some other functions aside
 * from scheduler functions. Sidecar helps to do this.
 * See /path/to/plugsched/examples/export_jump_sidecar.h for usage examples.
 */

EXPORT_SIDECAR(name_to_int, fs/proc/util.c, unsigned, const struct qstr *)
