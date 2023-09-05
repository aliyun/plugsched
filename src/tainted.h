#include <linux/kobject.h>

struct tainted_function {
	char *name;
	struct kobject *kobj;
};

extern struct tainted_function tainted_functions[];

extern int register_tainted_functions(struct kobject *);
extern void unregister_tainted_functions(void);