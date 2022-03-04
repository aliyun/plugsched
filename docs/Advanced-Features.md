# Sidecar
Since Linux kernel scheduler is tightly coupled with other subsystems, new features usually comes with small modifications to other subsystems. There are some examples,

- If you want to do some accounting for group-scheduling, you're likely to modify cpuacct.c as well.
- If you want to modify scheduling policies for kernel threads, you're likely to modify kthread.c as well.
- If you want to modify CPU affinity related policies, you may need to modify cpuset.c as well.

Sidecar resues infrastures of plugsched, so development with sidecar is nearly as easy as the core functionality of plugsched.

### How it works
Development with sidecar is done in a single file `kernel/sched/mod/sidecar.c`. 

Once you've finished coding this file. You can trigger a standard building. See [Quick Start](../README.md#quick-start). Plugsched then compiles this file, and link the compiled object into the finall module binary file.

Then when you install the scheduler module in the running system, plugsched treats all sidecar functions the same way as scheduler functions. IOW, plugsched does all process for sidecar functions mentioned in [Compile and install the scheduler](../README.md#compile-and-install-the-scheduler) except Scheduler state rebuild.

### Example
This example can be found in `/path/to/plugsched/examples/` too.

1. Log into the cloud server, and install some neccessary basic software packages.
2. Create a temporary working directory and download the source code of the kernel.
3. Startup the container, and spawn a shell.
4. Extract kernel source code.
5. Boundary analysis and extraction.
You can refer to [Quick Start](../README.md#quick-start) for more details about Step 1 ~ 5.

6. Test the sidecar functionality.

Paste this line to `kernel/sched/mod/export_jump_sidecar.h`

    EXPORT_SIDECAR(name_to_int, fs/proc/util.c, unsigned, const struct qstr *)

Paste this block to `kernel/sched/mod/sidecar.c`
```c
/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

/*
 * Copied from ./fs/proc/util.c and did a little modification to it
 */
#include <linux/dcache.h>

unsigned name_to_int(const struct qstr *qstr)
{
  const char *name = qstr->name;
  int len = qstr->len;
  unsigned n = 0;
  trace_printk("%s\n", name);      /* !! We added this line !! */

  if (len > 1 && *name == '0')
    goto out;
  do {
    unsigned c = *name++ - '0';
    if (c > 9)
      goto out;
    if (n >= (~0U-9)/10)
      goto out;
    n *= 10;
    n += c;
  } while (--len > 0);
  return n;
  out:
  return ~0U;
}
```

7. Compile and package it into a scheduler rpm package. (See [Quick Start](../README.md#quick-start))

``` shell
# plugsched-cli build /tmp/work/scheduler
```

8. Copy the scheduler rpm to the host, exit the container, and then install scheduler. (See [Quick Start](../README.md#quick-start))
