#include "tainted.h"

#undef TAINTED_FUNCTION
#define TAINTED_FUNCTION(func,sympos) 		\
	{ 					\
		.name = #func "," #sympos,	\
		.kobj = NULL,			\
	},

struct tainted_function tainted_functions[] = {
	#include "tainted_functions.h"
	{ .name = NULL, .kobj = NULL }
};

int register_tainted_functions(struct kobject *vmlinux_moddir)
{
	struct tainted_function *tf;

	for (tf = tainted_functions; tf->name; tf++) {
		tf->kobj = kobject_create_and_add(tf->name, vmlinux_moddir);
		if (!tf->kobj)
			return -ENOMEM;
	}

	return 0;
}

void unregister_tainted_functions(void)
{
	struct tainted_function *tf;

	for (tf = tainted_functions; tf->name; tf++) {
		if (!tf->kobj)
			return;
		kobject_put(tf->kobj);
	}
}
