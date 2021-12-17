#include <map>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <sstream>
#include <unistd.h>
#include <cstring>
#include <fcntl.h>
#include <gelf.h>

static void ERROR(std::string msg, bool elf_error, std::string extra="")
{
        if (elf_error)
                std::cerr << msg << ": " << elf_errmsg(-1) << std::endl;
        else
                std::cerr << msg << ": " << extra << std::endl;
        std::abort();
}

struct kallsym_t {
        char type;
        std::vector<unsigned long> addr;
};

typedef std::map<std::string, struct kallsym_t> kallsym_collection;
typedef std::map<std::string, int> sympos_collection;

static void resolve_ref(const char *fname, kallsym_collection &kallsyms, sympos_collection &symposes)
{
        int fd, sympos;
        Elf *elf;
        GElf_Sym sym;
        GElf_Shdr sh;
        Elf_Scn *scn = NULL;
        Elf_Data *data = NULL;
        size_t shstrndx, i;
        struct kallsym_t *kallsym;
        char *name, modified = 0;

        if (elf_version(EV_CURRENT) == EV_NONE )
                ERROR("ELF library initialization failed", true);

        fd = open(fname, O_RDWR);
        if (fd == -1)
                ERROR("open", true);

        elf = elf_begin(fd, ELF_C_RDWR, NULL);
        if (!elf)
                ERROR("elf_begin", true);

        elf_flagelf(elf, ELF_C_SET, ELF_F_LAYOUT);

        /* Find .symtab */
        if (elf_getshdrstrndx(elf, &shstrndx))
                ERROR("elf_getshdrstrndx", true);

        for (scn = elf_nextscn(elf, scn); scn; scn = elf_nextscn(elf, scn)) {
                if (!scn)
                        ERROR("scn NULL", true);
                if (!gelf_getshdr(scn, &sh))
                        ERROR("gelf_getshdr", true);
                if (!(name = elf_strptr(elf, shstrndx, sh.sh_name)))
                        ERROR("elf_strptr", true);
                if (!(data = elf_getdata(scn, NULL)))
                        ERROR("elf_getdata", true);
                if (!strcmp(name, ".symtab"))
                        break;
        }

        /* Find UND symbols in kallsyms */
        for (i=0; i < sh.sh_size / sh.sh_entsize; i++) {
                if (!gelf_getsym(data, i, &sym))
                        ERROR("gelf_getsym", true);
                if (!(name = elf_strptr(elf, sh.sh_link, sym.st_name)))
                        ERROR("elf_strptr", true);
                if (sym.st_shndx != SHN_UNDEF)
                        continue;
                if (kallsyms.find(name) == kallsyms.end())
                        continue;
                kallsym = &kallsyms[name];

                if (!strcmp(name, "kern_path") && kallsym->type != 'T')
                        continue;
                if (symposes.find(name) != symposes.end())
                        sympos = symposes[name];
                else /* Symbols which dont appear in sched_outsider should be global symbols */
                        sympos = 0;
                if (sympos == 0 && kallsym->addr.size() > 1)
                        ERROR("global symbol ambigouos is unresolvable.", false, name);
                if (sympos >  0 && kallsym->addr.size() < sympos)
                        ERROR("local symbol doens't have as many alternatives.", false, name);
                if (sympos > 0)
                        sympos --;
                /* Resolve UND symbols */
                sym.st_shndx = SHN_ABS;
                sym.st_value = kallsym->addr[sympos];
                modified = 1;
                if (gelf_update_sym(data, i, &sym) == -1)
                        ERROR("gelf_update_sym", true);
        }

        /* Write back elf file */
        if (modified) {
                if (!elf_flagdata(data, ELF_C_SET, ELF_F_DIRTY))
                        ERROR("elf_flagdata", true);
                if (elf_update(elf, ELF_C_WRITE) == -1)
                        ERROR("elf_update", true);
        }

        elf_end(elf);
        close(fd);
}

static void load_kallsyms(const char *fname, kallsym_collection &kallsyms)
{
        unsigned long long addr;
        char type;
        std::string name, line;
        std::ifstream f(fname);
        std::stringstream buffer;

        if (!f.is_open())
                ERROR("fopen kallsyms", false);

        while (getline(f, line)) {
                std::istringstream line_stream(line);
                line_stream >> std::hex >> addr >> type >> name;
                /* Reached modules */
                if (!line_stream.eof()) break;
                kallsyms[name].addr.push_back(addr);
        }

        f.close();
}

int main(int argc, const char **argv)
{
        kallsym_collection kallsyms;
        sympos_collection sched_outsider = {
                #include "sched_outsider.h"
        };

        load_kallsyms(argv[2], kallsyms);
        resolve_ref(argv[1], kallsyms, sched_outsider);

        return 0;
}
