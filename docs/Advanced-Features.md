# Sidecar
Since Linux kernel scheduler is tightly coupled with other subsystems, new features usually come with small modifications to other subsystems. There are some examples.

- If you want to add some metrics for group-scheduling, you're likely to modify cpuacct.c as well.
- If you want to modify scheduling policies for kernel threads, you're likely to modify kthread.c as well.
- If you want to modify CPU affinity related policies, you may need to modify cpuset.c as well.

Just like kpatch or livepatch, sidecar provides a way to live upgrade code at function granularity. With sidecar, developers can modify functions outside of scheduler boundary, such as cpuacct, kthread and cpuset. Sidecar reuses infrastuctures of plugsched, so development with sidecar is almost as easy as the core functionality of plugsched.

## How it works
Here is an example of how to use sidecar. If developers want to live upgrade function cpuusage_write() and cpuacct_free() in kernel/sched/cpuacct.c, they only need to configure boundary.yaml as below.

```
sidecar: !!pairs
    - cpuusage_write: kernel/sched/cpuacct.c
    - cpuacct_free: kernel/sched/cpuacct.c
```

After the configuration is complete, you can run init operation. See [Quick Start](../README.md#quick-start). Plugsched will generate a new cpuacct.c file under kernel/sched/mod/ directory automatically. Then you can change code of function cpuusage_write() and cpuacct_free() in new cpuacct.c freely. To make code change easier, some handy mechanisms are provided by plugsched.

1. The new cpuacct_free() can reference any functions or variables directly, plugsched will help to fix symbol location automatically;
2. Inline functions, data struct definition, header file including are reserved in new cpuacct.c, so the new cpuacct_free() can use them directly;
3. All variable definitons are translated into declarations, so the new cpuacct_free() can share data state with the running system.

Once the code changes are complete, you can run build operation. Plugsched then compiles all scheduler files and sidecar files, and link the compiled object into the final module binary file. When you install the scheduler module on the running system, plugsched treats all sidecar functions as same as the interface functions of scheduler. IOW, plugsched does all steps for sidecar functions mentioned in [Compile and install the scheduler](../README.md#compile-and-install-the-scheduler) except scheduler state rebuild.
