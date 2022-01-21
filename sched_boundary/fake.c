/**
 * Copyright 2019-2022 Alibaba Group Holding Limited.
 * SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
 */

/* other headers not list below are included by sched.h */

#include <linux/version.h>
#include "kernel/sched/sched.h"

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 12, 0)
#include "kernel/sched/sched-pelt.h"
#endif

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 19, 0)
#include "kernel/sched/pelt.h"
#endif
