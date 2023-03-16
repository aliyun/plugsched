Release 1.2.0
---

## Features

- Migrate from Python2 to Python3.
- Add tool compile_and_create_rpm.sh to make creating rpm from patch easier.
- Bump AnolisOS base docker image from 8.4 to 8.6.
- Speed up plugsched-cli init by optimizing the data collection.
- Allow more code to be customized by modularizing header files too.
- Generalize "boundary/" to be reused by the scheduler, eBPF and etc.
- Moving sidecar to init stage, which takes part in modularization. And improve its user experience.
- Support the kernel 5.10.
- Support the kernel 3.10. (x86_64 only)
- Reduce unneccessary callbacks by migrate sched_class for every task during update and rollback.
- Add yeild and sched_exec to interface function set.
- Print logging to stdio for better user experience when building the scheduler.
- Improve stability of extraction, avoiding nasty compiling errors. (tested with kernel/{bpf,sched} and 5.10, 4.19)
- Improve readability of extracted code by deleting __init function in unused macro branches.
- Support explicitly refer to the module's and vmlinux's symbol with __mod_ and __orig_ preifx respectively.
- Add conflict detection of ftrace or others hooked functions' header.


# Bugfix

- Fix a compiling error of stack_trace_save_tsk function passing a wrong type of argument.
- Fix the mempool error that the functions or variables in headers should be static to avoid conflicts with kernel code.
- Fix the unknown symbol error when installing module. Add the __used attribute to function pointers.
- Fix the bug of extraction of va_list and "..." parameter in export_jump.h.
- Fix the bug that some internal functions may be exported functions. Remove them outside.
- Fix removing cgroup file twice in syscall test case, that can cause test to fail.
- Fix panic and compiling bugs of stack-protector for aarch64.
- Fix test/ bug catch_error print error when exitcode=0 and ENOPERM bug when chrt in test_sched_syscall.
- Fix tainted_functions that includes __init functions.
- Fix the backslashes cannot be deleted when deleting code in extraction stage.
- Fix some bugs of the unnamed unions, enums and structures, that can cause the building errors.
- Fix the bug where undefined function sets contained optimized functions, which can cause installation failure.
- Fix some bugs of plugsched service. Keep service active and remove daemon-reload after starting service.
- Fix the bug that some variables only used in __init function will be removed, because scheduler may be use them.
- Fix some static interface functions been optimized that will be removed.
- Fix both strong and weak symbol existing, and treat overriden weak symbols as outsiders.
- Fix two race conditions bewteen redirecting and state rebuilding, that may cause panic.
- Fix the bug that num_online_cpus maybe changed between parallel_state_check_init and stop_machine, which may cause system hung.
- Fix a panic bug that stack check exits too early when insmod/rmmod.
- Fix the stack checker not checking the __sched functions, which may cause panic.
- Fix the bug about container that after installing some packages, containers refuses "podman exec -it".
- Forbid redirecting mangled functions.
- Fix the rebuilding of dying task that cannot found in init_tasklist, which can cause panic.

# Docs

- Fix some typoes.
- Update documentation for new sidecar implementation.

# Tests

- Remove test case for sched_rt_runtime_us.
- Add test case for stack pivot.
- Add test case for memory pressure.
- Add test case for reboot.
- Add test case for bare package run-time performance.

# Others

- Using inner function __sched_setscheduler() to replace the interface function sched_setscheduler().

Release 1.1.1
---

# BugFixes

- Fix wrong list of optimized functions caused by the bug `makefile` and `module.symvers` gets overwritten when `cli init`.

Release 1.1.0
---

## Features

- Add a test framework. And integrate 5 automated test cases into CI.
- Add fully support aarch64.
- Add fully support for AnolisOS 8.
- Add the per-cpu mempool infrastructure. It could be used like Linux kernel's per-cpu variable, as an extension to the existing mempool.
- Support installing multiple versions of scheduler in multiple kernel versions system.

## BugFixes

- Fix installing failure bug in the Quick Start, that it used to break once cloud OS images upgrade.
- Fix installing failure bug by ignoring confliction among hotfixes themselve, instead of with Plugsched.
- Fix installing failure bug that after rebooting, scheduler won't be loaded.
- Fix memory leak when using mempool.
- Fix user-unfriendly bug that experimental BAD scheduler.rpm (maybe poorly programmed by user) couldn't be erased.
- Fix potential panic bug caused by bottom-half of \_\_schedule by adding callees of the bottom-half as interfaces.
- Fix potential panic bug caused by GCC mangling (.isra, .constprop, .cold, etc.).
- Fix potential panic bug caused by kernel modules (mainly KVM), because modules didn't participate in modularization.
- Fix confliction checking with hotfixes. Now we check it each time scheduler is loaded, rather than only when installing rpm.
- Fix some warnings with no harm.

## Docs

- Clarify how Plugsched deals with data-upgrade (Rebuild or inherit or reinitialize), now this technique is illustrated clearly.
- Clarify that users shouldn't modify the size and semantic of data structure or its fields.
- Enrich the Quick Start, let users get hands on with sched-feature too.
- Update the architecture description figure.

# Other improvements

- Simplify sched\_boundary.yaml by removing useless keys (sched\_outsider & force\_outsider). Now users won't be confused about them.
- Improve user experience when debugging by outputting `make` result to the screen. Now users locate compiling errors more easily.