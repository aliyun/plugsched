It's recommended to work with `kernel-devel.rpm`, `kernel-debuginfo.rpm` and `kernel.srpm`. But if you don't have some of them, don't worry. There are some alternative workflows in the table below. Choose among them according to what resource (rpm, srpm, src code) you have.

| kernel-devel.rpm| kernel-debuginfo.rpm| kernel.srpm|kernel src|    |
|---- | -----------------|-----------------------|------------|-------------------|
|✅                       | ✅                               | ✅             |                            |[→ Standard Scenario](../README.md#quick-start)     | 
| ✅                       | ✅                               | ❌               | ✅                         |[→ Scenario 2](#scenario-2---with-rpm-src-code)     |
|❌                          |❌                                 |❌                | ✅                         |[→ Scenario 3](#scenario-3---with-src-code)     |

# Scenario 2 - With rpm, src code
Some distros don't provide `kernel.srpm` package. You may work with the combination of `kernel-devel.rpm`, `kernel-debuginfo.rpm` and `kernel src`.

1. Log into the cloud server, and install some neccessary basic software packages.
```shell
# yum install anolis-repos -y
# yum install podman git kernel-debuginfo-$(uname -r) kernel-devel-$(uname -r) --enablerepo=Plus-debuginfo --enablerepo=Plus -y
```
2. Create a temporary working directory and download the source code of the kernel.
```shell
# mkdir /tmp/work
# uname -r
4.19.91-25.2.an7.x86_64
# cd /tmp/work
# git clone --depth 1 --branch  4.19.91-25.2 https://gitee.com/anolis/cloud-kernel.git kernel
```
3. Startup the container, and spawn a shell.
```shell
# podman run -itd --name=plugsched -v /tmp/work:/tmp/work -v /usr/src/kernels:/usr/src/kernels -v /usr/lib/debug/lib/modules:/usr/lib/debug/lib/modules docker.io/plugsched/plugsched-sdk
# podman exec -it plugsched bash
# cd /tmp/work
```
4. Boundary analysis and extraction.
```shell
# plugsched-cli init 4.19.91-25.2.an7.x86_64 ./kernel ./scheduler
```
5. Do some modifications and build scheduler rpm. (Refer to [Quick Start](../README.md#quick-start))
6. Copy the scheduler rpm to the host, exit the container, and then install scheduler. (Refer to [Quick Start](../README.md#quick-start))

# Scenario 3 - With src code
This usually means you are experimenting on your own development kernel. You have only `kernel src` at hand. 

1. Log into the cloud server, and install some neccessary basic software packages.
```shell
# yum install anolis-repos -y
```
2. Create a temporary working directory and download the source code of the kernel.
```shell
# mkdir /tmp/work
# uname -r
4.19.91-25.2.an7.x86_64
# cd /tmp/work
# git clone --depth 1 --branch  4.19.91-25.2 git@gitee.com:anolis/cloud-kernel.git kernel
```
3. Startup the container, and spawn a shell.
```shell
# podman run -itd --name=plugsched -v /tmp/work:/tmp/work docker.io/plugsched/plugsched-sdk
# podman exec -it plugsched bash
# cd /tmp/work
```
4. Build the kernel
```shell
# pushd kernel
# cp arch/x86/configs/anolis_defconfig .config
# sed 's/EXTRAVERSION =/EXTRAVERSION = -25.2.an7.x86_64/g' -i Makefile
# make -j16
# popd
```
6. Boundary analysis and extraction.
```shell
# plugsched-cli dev_init /tmp/work/kernel ./scheduler
```
5. Do some modifications and build scheduler rpm. (Refer to [Quick Start](../README.md#quick-start))
6. Copy the scheduler rpm to the host, exit the container, and then install scheduler. (Refer to [Quick Start](../README.md#quick-start))
7. Install the new kernel and reboot to it
```shell
make install
reboot
```
