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