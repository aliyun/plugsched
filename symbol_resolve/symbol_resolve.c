#define _GNU_SOURCE
/* TODO If we get rid of linux/list.h then it's totally stand-alone. */
#include <linux/list.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <gelf.h>

#define ERROR(msg) do { \
        fprintf(stderr, msg ": %s\n", elf_errmsg(-1)); \
        exit(1); \
        } while (0);

struct kallsym_t {
        char type;
        char name[1024];
        unsigned long addr;
        struct list_head list;
};

typedef struct list_head kallsym_list;

static void resolve_ref(char *fname, kallsym_list *kallsyms)
{
        int fd;
        Elf *elf;
        GElf_Sym sym;
        GElf_Shdr sh;
        Elf_Scn *scn = NULL;
        Elf_Data *data = NULL;
        size_t shstrndx, i;
        struct kallsym_t *kallsym;
        char *name, modified = 0;

        if (elf_version(EV_CURRENT) == EV_NONE )
                ERROR("ELF library initialization failed");

        fd = open(fname, O_RDWR);
        if (fd == -1)
                ERROR("open");

        elf = elf_begin(fd, ELF_C_RDWR, NULL);
        if (!elf)
                ERROR("elf_begin");

        elf_flagelf(elf, ELF_C_SET, ELF_F_LAYOUT);

        /* Find .symtab */
        if (elf_getshdrstrndx(elf, &shstrndx))
                ERROR("elf_getshdrstrndx");

        for (scn = elf_nextscn(elf, scn); scn; scn = elf_nextscn(elf, scn)) {
                if (!scn)
                        ERROR("scn NULL");
                if (!gelf_getshdr(scn, &sh))
                        ERROR("gelf_getshdr");
                if (!(name = elf_strptr(elf, shstrndx, sh.sh_name)))
                        ERROR("elf_strptr");
                if (!(data = elf_getdata(scn, NULL)))
                        ERROR("elf_getdata");
                if (!strcmp(name, ".symtab"))
                        break;
        }

        /* Find UND symbols in kallsyms */
        for (i=0; i < sh.sh_size / sh.sh_entsize; i++) {
                if (!gelf_getsym(data, i, &sym))
                        ERROR("gelf_getsym");
                if (!(name = elf_strptr(elf, sh.sh_link, sym.st_name)))
                        ERROR("elf_strptr");
                if (sym.st_shndx != SHN_UNDEF)
                        continue;

                list_for_each_entry(kallsym, kallsyms, list) {
                        if (!strcmp(name, kallsym->name)) {
                                if (!strcmp(name, "kern_path") && kallsym->type != 'T')
                                        continue;
                                /* Resolve UND symbols */
                                sym.st_shndx = SHN_ABS;
                                sym.st_value = kallsym->addr;
                                modified = 1;
                                if (gelf_update_sym(data, i, &sym) == -1)
                                        ERROR("gelf_update_sym");
                                break;
                        }
                }
        }

        /* Write back elf file */
        if (modified) {
                if (!elf_flagdata(data, ELF_C_SET, ELF_F_DIRTY))
                        ERROR("elf_flagdata");
                if (elf_update(elf, ELF_C_WRITE) == -1)
                        ERROR("elf_update");
        }

        elf_end(elf);
        close(fd);
}

static void load_kallsyms(char *fname, kallsym_list *kallsyms)
{
        FILE *f = fopen(fname, "r");
        char *line = NULL;
        struct kallsym_t *next;
        size_t len;

        if (f == NULL)
                ERROR("fopen kallsyms");

        INIT_LIST_HEAD(kallsyms);
        while (getline(&line, &len, f) != -1) {
                next = (struct kallsym_t*)calloc(1, sizeof(struct kallsym_t));
                sscanf(line, "%lx %c %s", &next->addr, &next->type, next->name);
                list_add_tail(&next->list, kallsyms);
        }

        fclose(f);
}

static void free_kallsyms(kallsym_list *kallsyms)
{
        struct kallsym_t *next, *tmp;
        list_for_each_entry_safe(next, tmp, kallsyms, list) {
                list_del(&next->list);
                free(next);
        }
}

int main(int argc, char **argv)
{
        kallsym_list kallsyms;

        load_kallsyms(argv[2], &kallsyms);
        resolve_ref(argv[1], &kallsyms);
        free_kallsyms(&kallsyms);

        return 0;
}
