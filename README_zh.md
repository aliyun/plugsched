## Plugsched: Linux 内核调度器子系统热升级
plugsched 是 Linux 内核调度器子系统热升级的 SDK，它可以实现在不重启系统、应用的情况下动态替换调度器子系统，毫秒级 downtime 。plugsched 可以对生产环境中的内核调度特性动态的进行增、删、改，以满足不同场景或应用的需求，且支持回滚。

## Motivation
* **应用场景不同，最佳调度策略不同：** 在云场景下，调度策略的优化比较复杂，不存在“一劳永逸”的策略。因此，允许用户定制调度器用于不同的场景是必要的。
* **调度器迭代慢：** Linux 内核经过很多年长时间的更新迭代，它的代码变得越来越繁重，而调度器是内核最核心的子系统之一，它的结构复杂，与其它子系统紧密耦合，这使得开发和调试变得越发困难。Linux 很少增加新的调度类，尤其是不太可能接受非通用或场景针对型的调度器。plugsched 可以让调度器与内核解耦 ，开发人员可以只关注调度器的迭代开发。
* **内核升级困难：** 调度器内嵌在内核中，因此应用调度器的修改需要更新内核。内核发布周期通常是数月之久，这将导致新的调度器无法及时应用在系统中。再者，要在集群中升级新内核，涉及迁移业务和停机升级，对业务方来说代价昂贵。
* **无法升级子系统：** kpatch 和 livepatch 是函数粒度的热升级方案，可表达能力较弱，不能实现复杂的代码改动；对于 eBPF，当前调度器还不支持 ebpf hook，将来即使支持，也只是局部策略的修改。

## How it works
 调度器子系统在内核中并非是一个独立的模块，而是内嵌在内核中，与内核其它部分紧密相连。plugsched 采用“模块化”的思想：它提供了边界划分程序，确定调度器子系统的边界，把调度器从内核代码中提取到独立的目录中，开发人员可对提取出的调度器代码进行修改，然后编译成新的调度器内核模块，动态替换内核中旧的调度器。

对于函数而言，它对外呈现了一些接口函数。通过替换内核中的这些函数，内核就可以绕过原有的执行逻辑进入新的调度器模块中执行，即可完成函数的升级。在模块中的函数要么是接口函数，要么是内部函数，其它函数都是外部函数。

对于数据，调度器模块重新初始化私有数据，并从前一个调度器继承共享数据。大多数重要数据（运行队列状态和调度类状态）通过状态重建技术重新初始化，而不仅仅是重置内存，它们因此自动变成了 private 数据。为了灵活性， plugsched 允许用户手动将其余部分数据定义为私有数据。然而，手动定义的私有数据只会被清零。因此，为了简单起见，默认情况下，其它数据是共享数据。

对于数据而言，用户不仅想知道数据是如何初始化的，还想知道是否可以修改数据本身或者它的语义。Plugsched 没有对全局变量和局部变量设置严格的规则，因此用户可以修改数据本身或它们的语义。但是结构体不同。首先，plugsched 将只被调度器访问的结构体成员分类为内部成员，其他为非内部成员。调度器模块允许修改内部成员的语义，禁止修改非内部成员的语义。如果结构体所有成员都是内部成员，则调度器模块允许修改整个结构体。但是，我们建议使用结构体中的保留字段，而不是修改现有成员。

比如，修改 rq->lock 的状态会修改数据本身，而用 rq->lock 存其他数据，是修改了它的语义。而缩短 rq 结构体的大小，相当于修改了 rq 中的一些成员。但是由于 rq->lock 被其它子系统使用，因此它是一个非内部成员，所以不允许修改 rq->lock 或 rq 结构体。

### 边界提取
调度器本身并不是模块，因此需要明确调度器的边界才能将它模块化，边界划分程序根据边界配置信息从内核源代码中将调度器模块的代码提取出来。边界配置信息主要包含代码文件、接口函数等信息。最终将边界内的代码提取到独立的目录中，主要分为以下过程：
* 信息收集

  在 Linux Kernel 编译过程中，使用 gcc-python-plugin 收集边界划分相关的信息，比如符号名、位置信息、符号属性及函数调用关系等；
* 边界分析

  对收集的信息进行分析，根据边界配置文件，计算调度器模块的代码和数据的边界，明确哪些函数、数据在调度器边界内部；
* 代码提取

  再次使用 gcc-python-plugin 将边界内的代码提取到 kernel/sched/mod 目录作为调度器模块的 code base。

### 调度器模块开发
边界提取之后，调度器模块的代码被放到了独立的目录中，开发人员可修改目录中的调度器代码，根据场景定制调度器，开发过程的注意事项请看 [Limitations](#limitations) 小结。

### 编译及安装调度器
开发过程结束后，调度器模块代码与加载/卸载及其它相关功能的程序编译成内核模块，并生成调度器rpm包。安装后将会替换掉内核中原有的调度器，安装过程会经历以下几个关键过程：
* **符号重定位：** 对模块中的 undefined 符号进行重定位；
* **栈安全检查：** 类似于 kpatch，函数替换前必须进行栈安全检查，否则会出现宕机的风险。plugsched 对栈安全检查进行了并行优化，提升了栈安全检查的效率，降低了停机时间；
* **接口函数替换：** 用模块中的接口函数动态替换内核中的函数；
* **调度器状态重建：** 采用通用方案自动同步新旧调度器的状态，极大的简化数据状态的一致性维护工作。

![20220225173717](https://user-images.githubusercontent.com/33253760/155691850-20817e95-afec-4544-a35f-a284896c973c.jpg)

## Use Cases
1. 快速开发、验证、上线新特性，稳定后放入内核主线；
2. 针对不同业务场景做定制优化，以 RPM 包的形式发布和维护非通用调度器特性；
3. 统一管理调度器热补丁，避免多个热补丁之间的冲突而引发故障；

## Quick Start
目前，plugsched 默认支持 Anolis OS 7.9 ANCK 系统发行版，其它系统需要[调整边界配置](./docs/Support-various-Linux-distros.md)。为了减轻搭建运行环境的复杂度，我们提供了的容器镜像和 Dockerfile，开发人员不需要自己去搭建开发环境。为了方便演示，这里购买了一台阿里云 ECS（64CPU + 128GB），并安装 Anolis OS 7.9 ANCK 系统发行版，我们将会对内核调度器进行热升级。

1. 登陆云服务器后，先安装一些必要的基础软件包：
```shell
# yum install anolis-repos -y
# yum install podman kernel-debuginfo-$(uname -r) kernel-devel-$(uname -r) --enablerepo=Plus-debuginfo --enablerepo=Plus -y
```

2. 创建临时工作目录，下载系统内核的 SRPM 包：
```shell
# mkdir /tmp/work
# uname -r
4.19.91-25.2.an7.x86_64
# cd /tmp/work
# wget https://mirrors.openanolis.cn/anolis/7.9/Plus/source/Packages/kernel-4.19.91-25.2.an7.src.rpm
```

3. 启动并进入容器：
```shell
# podman run -itd --name=plugsched -v /tmp/work:/tmp/work -v /usr/src/kernels:/usr/src/kernels -v /usr/lib/debug/lib/modules:/usr/lib/debug/lib/modules docker.io/plugsched/plugsched-sdk
# podman exec -it plugsched bash
# cd /tmp/work
```

4. 提取 4.19.91-25.1.al7.x86_64 内核源码：
```shell
# plugsched-cli extract_src kernel-4.19.91-25.2.an7.src.rpm ./kernel
```

5. 进行边界划分与提取：
```shell
# plugsched-cli init 4.19.91-25.2.an7.x86_64 ./kernel ./scheduler
```

6. 提取后的调度器模块代码在 ./scheduler/kernel/sched/mod 中，简单修改 __schedule 函数，然后编译打包成调度器 rpm 包：
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

7. 将生成的 rpm 包拷贝到宿主机，退出容器，并安装调度器包：
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
**Q: 默认边界配置下， 边界划分后的调度器模块里面有什么东西？**

包含以下内容：

- [ ] autogroup
- [ ] cpuacct
- [ ] cputime
- [X] sched debug
- [X] sched stats
- [X] cfs rt deadline idle stop sched class
- [X] sched domain topology
- [X] sched tick
- [X] scheduler core

**Q: 调度器热升级可以修改哪些函数？**

边界提取结束后，kernel/sched/mod 目录里的文件中定义的函数都是可以修改的，比如，quick start 示例中，调度器模块可修改的范围包含 1k+ 个函数。但是有些需要注意的地方，请看 [Limitations](#limitations) 章节。

**Q：调度器模块的边界可以修改吗？**

可以修改，通过修改边界配置文件可修改调度器边界，比如修改代码文件、接口函数等，请参考[这里](./docs/Support-various-Linux-distros.md)。注意，若调整了调度器边界，上线前需要做严格的测试。

**Q：plugsched 支持哪些内核版本？**

理论上 plugsched 是与内核版本解耦的，我们测试过的内核版本有 3.10 和 4.19，其它版本需开发人员自行适配与测试。

**Q：可以修改头文件中的函数吗？**

可以。我们对头文件中的函数进行了边界划分，kernel/sched/mod 目录中的头文件不可修改的函数已被加上“DON'T MODIFY FUNCTION ******, IT'S NOT PART OF SCHEDMOD” 的注释，其它函数可以修改。

**Q：可以修改结构体吗？**

若结构体中存在共享成员，则不可以修改结构体。若整个结构体是私有的，则可以修改结构体，请参考 How it works 中对数据的描述。修改结构体时，首先推荐使用结构体中的预留字段；其次再考虑复用结构体中的私有成员。

**Q：内核调度器被替换后会有性能回退吗？**

调度器模块本身的 overhead 很小，可以被忽略。其次，还取决于开发人员对调度器的修改。经过 benchmark 测试，如果不加任何修改，是没有性能影响的；

**Q：加载模块时停机时间长吗？有多少？**

这取决于当前系统的负载及进程数量，进程数量越重，负载越多，downtime 越长。在我们的测试中，104 核 CPU 下 10k+ 的进程数量，downtime 不到 10 ms。

**Q：这和 kpatch 有什么区别？是 kpatch 的一种优化吗？**

kpatch 是函数粒度的热升级，plugsched 是子系统范围的热升级，有些功能和实现是无法通过 kpatch 的优化做到的，比如 kpatch 无法修改 __schedule 函数、无法同时修改上千个函数等。

**Q：和 kpatch 的热升级有冲突吗？**

有冲突，如果 kpatch 和 plugsched 修改的范围有交集，重叠的部分会被 plugsched 覆盖掉。不过我们设计了可用于生产环境的冲突检测机制。

**Q：可以修改调度器边界之外的函数吗？**

可以，我们提供了 [sidecar](./docs/Advanced-Features.md) 机制可以同时修改边界之外的函数。比如，有些 hotfix 既修改了调度器，又修改了 cpuacct 中的内容，可以使用 sidecar 机制升级 cpuacct 中的内容。

## Supported Architectures
- [X] x86-64
- [ ] aarch64: plan to do

## Limitations
* 不可修改 init 函数，因为 init 函数在系统重启后被释放掉，需要初始化的过程请在加载模块时执行；
* 不可修改接口函数的签名，也不可删除接口函数，如果要删除，可以将函数修改为空函数；
* 不可修改任何带有“DON'T MODIFY FUNCTION ******, IT'S NOT PART OF SCHEDMOD”注释的函数；
* 不可随意修改结构体及成员的语义，需要修改时请参考 working/sched_boundary_doc.yaml 文档进行；
* 加载调度器模块后，不可直接 hook 内核中属于调度器模块范围内的函数，比如 perf 或者 ftrace 等工具，需要时请指定 scheduler.ko 模块；

## License
plugsched is a linux kernel hotpluggable scheduler SDK developed by Alibaba and licensed under the GPLv3+ License or BSD-3-Clause License. This product contains various third-party components under other open source licenses. See the NOTICE file for more information.
