Plugsched supports various Linux distros through config files. Config files are consumed by [Code Extraction](../README.md#boundary-extraction) of plugsched, to generate various scheduler module source code.

The default config file is designed for [Anolis OS 7 ANCK](https://openanolis.cn/)，so plugsched can work on it out-of-the-box. And If you want the scheduler module to be plugged into other Linux distros, you should define the scheduler boundary properly first.

**========================= NOTE =========================** 

Prior knowledge about [scheduler boundary](../README.md#how-it-works) is required before reading this chapter.

# Where to add config for a given linux kernel
Config files for Linux kernels are put in `configs` directory. And they're organized as a flat list fashion,

	configs
	├── 3.10
	├── 4.19
	├── 4.19.91
	└── your-linux-kernel-version

The init stage is usually triggered with the following command, please refer to [Quick Start](../README.md#quick-start).

```bash
plugsched-cli init $version $kernel_src $sched_mod
```

plugsched then searches in the `configs/` directory, by the `version` given in the command line. Instead of full-text matching search, plugsched does Longest Common Prefix (LCP) search. 

For the example above, 4.19.91-1.x86_64 matches 4.19.91. And 4.19.64-5.x86_64 matches 4.19. And if you want to add a config for kernel 4.19.91-12.x86_64, you can create a folder such as 4.19.91-12,

	configs
	├── 3.10
	├── 4.19
	├── 4.19.91
	└── 4.19.91-12 (*)

And because 4.19.91-12.x86_64 shares the longest common prefix length with 4.19.91-12, which is 10, plugsched will choose `configs/4.19.91-12` for the kernel 4.19.91-12.x86_64.

And this is how plugsched does config matching, and how to add config for you own kernel.

# How to write the config file
scheduler_boundary.yaml in configs defines a scheduler boundary for a specific linux kernel,

	configs
	└── 4.19.91
	    └── sched_boundary.yaml

It's structured as the yaml file below.

(Note that, interface, insider and outsider functions mentioned below are illustrated in [How it works](../README.md#how-it-works). Please refer to it first.)

```yaml
# List files in kernel/sched, but only those you concern.
# And they are all the files that will be extracted (See How It Works in README)
mod_files:
    - **
# Usually syscall prefixes. *Don't* modify this unless you know what you're doing.
interface_prefix:
    - **
function:
    # List interface functions. Insiders and outsiders will be calculated accordingly.
    interface:
        - **
global_var:
    # Static variables are private by default. Announce them as public explicitly here.
    extra_public:
        - **
    # Global variables are public by default. Announce them as private explicitly here.
    force_private:
        - **
```

It's recommended to take the default config file as a template, and do your customization over it. Then when you start working on defining scheduler boundary, you should ask yourself several questions,

- **Does my kernel have some different files from kernels listed in configs/ directory?**
For example, core_sched.c was added to the kernel since version 5.14. And apparently, it is one scheduler file. You can simply add core_sched.c to `mod_files` to make it part of the scheduler module.

- **Do I want to inherit some variable from the original kernel**
For global variables, this is useful when in some cases. In the cases you want the scheduler to be *clean*. You don't want the scheduler module to inherit some state (meaning variable) from the original kernel. In this case, you should add these variable names to `force_private`.
On the contrary, static variables are all private by default (This is a flaw that needs to be fixed). However we usually want to inherit all variables. So all static variables better be added to `extra_public`.

- **Which functions must be modifiable in the scheduler module?**
This will guide you tuning interface functions. Because the most important rule to verify the correctness of interface function list is, does they cover all functions that you want to modify?
You will go through a little iterations of the workflow below to get the satisfied interface function list.

**The workflow to try tune sched_boundary.yaml** 

	Copy from anolis's template
	        |
	        v
	modify sched_boundary.yaml <----------------------------------------+
	    	|                                                           |
	    	v                                                           |
	plugsched init                                                      |
	    	|                                                           |
	    	v                                                           |
	Check working/sched_boundary_extract.yaml and kernel/sched/mod/     |
	    	|                                                           |
	    	v                                                           |
	Get the satisfied boundary result   -----Y--------------------------)---->  Done
	    	|N                                                          |
	    	v                                                           |
	Locate those unexpected sched_outsider/private variable             |
	    	|                                                           |
	    	v                                                           |
	Decide why they become so  -----------------------------------------+

The basic advice for you to define sched boundary
- Functions called by many other functions in other subsystems should be `interface`.
- Variables should all be defined as `public`, unless you know what you're doing.

# What are other files in config directory

- `dynamic_springboard.patch` Internal implementation. No need to concern.
- `pre_extract.patch` If your kernel has some strange non-standard code style, plugsched might be confused. This patch is used to refactor code styles. This file mainly serves as workarounds to strange bugs.
- `post_extract.patch` Internal implementation. No need to concern

dynamic_springboard.patch and post_extract.patch are mentioned as "Internal implementaion". But sometimes due to different kernel code bases. They need to be adjusted to make patch utility work.
