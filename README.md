## Plugsched: live update Linux kernel scheduler
Plugsched is a SDK that enables live updating the Linux kernel scheduler. It can dynamically replace the scheduler subsystem without rebooting the system or applications, with milliseconds downtime. Plugsched can help developers to dynamically add, delete and modify kernel scheduling features in the production environment, which allows customizing the scheduler for different specific scenarios. The live-update capability also enables rollback.

## Motivation
* **Different policies fit for differnt scenarios:** In the scenario of cloud-computing, optimizing scheduling policies is complex, and an one-fit-all strategy does not exist. So, it is necessary to allow users to customize the scheduler for different scenarios.
* **Scheduler evolved slowly :** Linux kernel has been evolved and iterated for many years, and has a heavy code base. Scheduler is one of the core subsystems of the kernel and its structure is complex and tightly coupled with other OS subsystems, which makes the development and debugging even harder. Linux rarely merges new scheduling classes, and would be especially unlikely to accept a scenario-specific or non-generic scheduler. Plugsched can decouple the scheduler from the kernel, and developers can only focus on the iterative development of the scheduler.
* **Updating kernel is hard:** The scheduler is built into the kernel, so applying changes to the scheduler requires updating the kernel. The kernel release cycle is usually several months, which makes the changes not able to be deployed quickly. Furthermore, updating kernel is even more expensive in the cluster, which involves application migration and machine downtime.
* **Unable to update a subsystem:** kpatch and livepatch are live update techniques of function granularity, which have weak expressive ability and cannot implement complex code changes. For eBPF, it doesn't support the scheduler well yet, and even if it were, it would only allow small modifications to the scheduling policies.

## How it works
The scheduler subsystem is built into the kernel, not an independent module . And it's highly coupled to other parts of the kernel. Plugsched takes advantage of the idea of modularization: it provides a boundary analyzer that determines the boundary of the scheduler subsystem and extracts the scheduler from the kernel code into a separate directory. Developers can modify the extracted scheduler code and compile it into a new scheduler module and dynamically replace the old scheduler in the running system.

For functions, the scheduler module exports some **interface** functions. By replacing these functions in the kernel, the kernel can bypass the original execution logic and enter the new scheduler module, thereby completing the function update. Functions compiled in the scheduler module are either interface functions, or **insiders**. Other functions are all called **outsiders**.

For data, the scheduler module re-initializes **private data** and inherits **shared data** from the previous scheduler. Re-initializing is more than resetting the memory, but through Scheduler State Rebuild technique. Most of the important data (runqueue state and sched class state) is handled by Scheduler State Rebuild technique, making them private automatically. And plugsched allows users to manually define some of the rest of the data as private data for flexibility. However, the manually defined private data merely means resetting the memory. So by default, the rest of the data is shared data for simplicity.

Also for data, users want to know not only how is the data initialized, but also whether they can modify some data itself, or the semantic of it. No strict rules are set on global variables and stack variables yet, so users can modify either the data themselves or the semantics of them. But data structures are different. First, plugsched classifies struct fields which is accessed only by the scheduler as **inner-fields**, others as **non-inner-fields**. The scheduler module allows modifying the semantics of inner fields, and forbids to modify the semantics of non-inner fields. And the scheduler module even allows modifing the size of the whole data structure if all fileds are inner fileds. Last but most important, we recommend using reserved fields of data structures, rather than modifying existing ones.

For example, modifying the state of `rq->lock` only changes its data, while using `rq->lock` to store something else changes its semantics, and reducing the size of `struct rq` is equivalent to modifying many members of `struct rq`. But since `rq->lock` is accessed by many subsystems, making it non-inner data. Users are forbidden to modify `rq->lock`, or shrink the size of `struct rq`.

### Boundary Extraction
The scheduler itself is not a module, so it is necessary to determine the boundary of the scheduler for modularization. The boundary analyzer extracts the scheduler code from the kernel source code according to the boundary configuration information. The configuration mainly includes source code files, the interface functions, etc. Finally, the code within the boundary is extracted into a separate directory.  The process is mainly divided into the following steps.

* Gather Information
  Compile the Linux kernel and use gcc-python-plugin to collect information related to boundary analysis, such as symbol names, location information, symbol attributes, and function call graph, etc.

* Boundary Analysis
  Analyze the gathered information, calculate the code and data boundaries of the scheduler according to the boundary configuration, and determine which functions and data are within the scheduler boundary.

* Code Extraction
  Use gcc-python-plugin again to extract the code within the boundary into the kernel/sched/mod directory as the code base for the new scheduler module.

### Develop the scheduler
After the extraction, the scheduler's code is put in a separate directory. Developers can modify the code and customize the scheduler according to different scenarios. Please see [Limitations](#limitations) for precautions during development.

### Compile and install the scheduler
After the development, the scheduler with loading/unloading and other related code will be compiled into a kernel module, then be packaged in RPM. After installation, the original scheduler built in the kernel will be replaced. The installation will go through the following key steps.
* **Symbol Relocation:** relocate the undefined symbols in scheduler module.
* **Stack Safety Check:** Like kpatch, stack inspection must be performed before function redirection, otherwise the system may crash. Plugsched optimizes stack inspection in parallel, which improves efficiency and reduces downtime.
* **Redirections:** Dynamically replace interface functions in kernel with corresponding functions in module.
* **Scheduler State Rebuild:** Synchronize the state between the new and old scheduler automatically, which  greatly simplifies the maintenance of data state consistency.

![Architecture](https://user-images.githubusercontent.com/33253760/156824976-c15684be-467b-45ac-abd6-976a9a5d542f.jpg)

## Use Cases
1. Quickly develop, verify and release new features, and merge them into the kernel mainline after being stable.
2. Customize and optimize for specific business scenarios, publish and maintain non-generic scheduler features using RPM packages.
3. Unified management of scheduler hotfixes to avoid conflicts caused by multiple hotfixes.

## Quick Start
Plugsched currently supports Anolis OS 7.9 ANCK by default, and other OS need to adjust the [boundary configrations](./docs/Support-various-Linux-distros.md). In order to reduce the complexity of building a running environment, we provide container images and Dockerfiles, and developers do not need to build a development environment by themselves. For convenience, we purchased an Alibaba Cloud ECS (64CPU + 128GB) and installed the Anolis OS 7.9 ANCK. We will live update the kernel scheduler.

1. Log into the cloud server, and install some neccessary basic software packages.
```shell
# yum install anolis-repos -y
# yum install podman kernel-debuginfo-$(uname -r) kernel-devel-$(uname -r) --enablerepo=Plus-debuginfo --enablerepo=Plus -y
```

2. Create a temporary working directory and download the source code of the kernel.
```shell
# mkdir /tmp/work
# uname -r
4.19.91-25.2.an7.x86_64
# cd /tmp/work
# wget https://mirrors.openanolis.cn/anolis/7.9/Plus/source/Packages/kernel-4.19.91-25.2.an7.src.rpm
```

3. Startup the container, and spawn a shell.
```shell
# podman run -itd --name=plugsched -v /tmp/work:/tmp/work -v /usr/src/kernels:/usr/src/kernels -v /usr/lib/debug/lib/modules:/usr/lib/debug/lib/modules docker.io/plugsched/plugsched-sdk
# podman exec -it plugsched bash
# cd /tmp/work
```

4. Extract kernel source code.
```shell
# plugsched-cli extract_src kernel-4.19.91-25.2.an7.src.rpm ./kernel
```

5. Boundary analysis and extraction.
```shell
# plugsched-cli init 4.19.91-25.2.an7.x86_64 ./kernel ./scheduler
```

6. The extracted scheduler code is in ./scheduler/kernel/sched/mod now, simply modify the __schedule function, and then compile and package it into a scheduler rpm package.
```diff
diff --git a/kernel/sched/mod/core.c b/kernel/sched/mod/core.c
index f337607..88fe861 100644
--- a/kernel/sched/mod/core.c
+++ b/kernel/sched/mod/core.c
@@ -3235,6 +3235,8 @@ static void __sched notrace __schedule(bool preempt)
 	struct rq *rq;
 	int cpu;
 
+	printk_once("scheduler: Hi, I am the new scheduler!\n");
+
 	cpu = smp_processor_id();
 	rq = cpu_rq(cpu);
 	prev = rq->curr;
```
```shell
# plugsched-cli build /tmp/work/scheduler
```

7. Copy the scheduler rpm to the host, exit the container, and then install scheduler.
```text
# cp /usr/local/lib/plugsched/rpmbuild/RPMS/x86_64/scheduler-xxx-4.19.91-25.2.an7.yyy.x86_64.rpm /tmp/work
# exit
exit
# rpm -ivh /tmp/work/scheduler-xxx-4.19.91-25.2.an7.yyy.x86_64.rpm
# dmesg ｜ tail -n 10
[  878.915006] scheduler: total initialization time is        5780743 ns
[  878.915006] scheduler module is loading
[  878.915232] scheduler: Hi, I am the new scheduler!
[  878.915232] scheduler: Hi, I am the new scheduler!
[  878.915990] scheduler load: current cpu number is               64
[  878.915990] scheduler load: current thread number is           626
[  878.915991] scheduler load: stop machine time is            243138 ns
[  878.915991] scheduler load: stop handler time is            148542 ns
[  878.915992] scheduler load: stack check time is              86532 ns
[  878.915992] scheduler load: all the time is                 982076 ns
```

## FAQ
**Q: Under the default boundary configuration, what does the scheduler contain after boundary extraction?**

Contains the following:

- [ ] autogroup
- [ ] cpuacct
- [ ] cputime
- [X] sched debug
- [X] sched stats
- [X] cfs rt deadline idle stop sched class
- [X] sched domain topology
- [X] sched tick
- [X] scheduler core

**Q: Which functions can I modify?**

After boundary extraction, all functions defined in the files in the kernel/sched/mod directory can be modified. For example, in the example of Quick Start, 1K+ functions of the scheduler can be modified. However, there are some precautions, please refer to [Limitations](#limitations).

**Q: Can I modify the scheduler boundary?**

Yes. The scheduler boundary can be modified by editing boundary configuration, such as modifying the source code file, interface function, etc. Please refer to [here](./docs/Support-various-Linux-distros.md). Note that if the scheduler boundary is adjusted, strictly testing is required before installing the scheduler into production environment.

**Q: What kernel versions does plugsched support?**

Theoretically, plugsched is decoupled from the kernel version. The kernel versions we have tested are 3.10 and 4.19. Other versions need to be adapted and tested by developers.

**Q: Can I modify functions defined in header files?**

Yes. Boundary analyzer also works for header files. Functions in kernel/sched/mod/\*.h can be modified, except those follows with a comment "DON'T MODIFY FUNCTION ****** ,IT'S NOT PART OF SCHEDMOD".

**Q: Can structures be modified?**

It depends. If there are any non-inner-fields in the structure, the structure can't be modified. On the contrary, if there aren't any non-inner-fields in the structure, it can be modified.

When modifying a structure, it's most recommended to use the reserved fields in the structure, and secondly recommended to reuse the inner-fields in the structure.

**Q: Will there be a performance regression when the kernel scheduler is replaced?**

The overhead incurred by plugsched can be ignored,  and the performance regression is mainly depend on the code modificated by developers. After the benchmark test, the new scheduler has no performance impact if no modification was applied.

**Q: Is there any downtime when loading scheduler modules? how many?**

It depends on the current system load and the number of threads. In our tests, we have 10k+ processes running on a 104 logical CPU machine. And the downtime is less than 10ms.

**Q: What's the difference between plugsched and kpatch? Do we achieve the same goal by optimizing kpatch? **

kpatch is live updating for function granularity, while plugsched for subsystem-wide. Some capabilities cannot be achieved through kpatch optimization. For example, kpatch can not modify the __schedule function, and can not modify thousands of functions at the same time.

**Q: Does plugsched conflict with the hotfix of Kpatch?**

Yes. The overlaped part between plugsched and kpatch will be overwrote by plugsched. However, we have designed conflict detecting mechanisms that can be used in the production environment.

**Q: Can I modify a function outside the scheduler boundary?**

Yes. We provide the [sidecar](./docs/Advanced-Features.md) mechanism to modify functions outside the boundary. For example, if we want to modify both the scheduler and cpuacct , we can use the sidecar to modify cpuacct.

## Supported Architectures
- [X] x86-64
- [ ] aarch64: plan to do

## Limitations
* Cannot modify the init functions because they have been released after rebooting. If you need to, please do it in module initialization.
* The interface function signature cannot be modified. And the interface function can not be deleted, but you can modify it to make it an empty function.
* Can not modify the functions with "DON'T MODIFY FUNCTION ******, IT'S NOT PART OF SCHEDMOD" comment;
* We don't recommend modifying structures and semantics of their members at well. If you really need to, please refer to the working/sched_boundary_doc.yaml documentation.
* After the scheduler module is loaded, you cannot directly hook a kernel function within the scheduler boundary, such as perf or ftrace tools. If you need to, please specify the scheduler.ko module in the command.

## License
plugsched is a linux kernel hotpluggable scheduler SDK developed by Alibaba and licensed under the GPLv3+ License or BSD-3-Clause License. This product contains various third-party components under other open source licenses. See the NOTICE file for more information.
