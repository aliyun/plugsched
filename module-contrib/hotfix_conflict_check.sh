#!/bin/bash

#This is the name of this script itself.
#
script="${0##*/}"

# The  arguments passed to this script are the parent
# directories to be searched, e.g: /home/me /usr/local
# Check if any given. If not, error out.
#

#if [ -z "$1" ] ; then
#    echo "Usage: $script" >&2
#    exit 1
#fi

# Create a temporary directory. For accurate results we need
# to be sure it is empty. This is one way to do this: create
# an temp dir that is garanteed to not exist yet.
#
# If you want to keep the "outputdir" with the results, make sure
# output dir you use does not contain files you want to keep, because
# files will be removed from it by this script! Better yet, make
# sure it is empty before starting this script.
#
outputdir=$(mktemp --tmpdir -d "${script}.XXXXXXXXXX")   # ensures new unique directory
trap "rm -r $outputdir" INT HUP QUIT ABRT ALRM TERM EXIT # ensures it is deleted when script ends

# Search the directories given as arguments, and process
# the paths of alle files one by one in a loop.
#
#find "$@" -type f | while read path ; do
find /sys/kernel/kpatch/patches/*/functions -type d -not -path "*/functions" 2>/dev/null | while read path ; do
#   /sys/kernel/kpatch/patches/kpatch_D689377/functions/blk_mq_update_queue_map -> blk_mq_update_queue_map
    func="${path##*/}"
    echo "$path" >>"${outputdir}/${func}.txt"
done

# deal with kernel live patch
find /sys/kernel/livepatch/*/ -type d -path "*,[0-9]" 2>/dev/null | while read path ; do
	# /sys/kernel/livepatch/kpatch_5928799/ip6_tables/translate_compat_table,1 -> translate_compat_table,1
    func="${path##*/}"
	# translate_compat_table,1 -> translate_compat_table
	func=`echo $func | awk -F , '{print $1}'`
    echo "$path" >>"${outputdir}/${func}.txt"
done

# deal with manual hotfix that has sys directory entry
find /sys/kernel/manual_*/ -type d -not -path "*manual_*/" 2>/dev/null | while read path ; do
    func="${path##*/}"
    echo "$path" >>"${outputdir}/${func}.txt"
done

# deal with manual hotfix that does not have sys directory entry, i.e, the early days implemenation
for func in `cat /proc/kallsyms | grep '\[kpatch_' | grep -v __kpatch | awk '{print $3}' | grep -v 'patch_'`;
do

    grep "e9_$func" /proc/kallsyms > ${outputdir}/out

    if [ -s "${outputdir}/out" ]; then
        cat ${outputdir}/out | awk '{print $4}' >> "${outputdir}/${func}.txt"
    else
        :
    fi
done


# Finally, if you want to end up with only file names that
# occur more than once, delete all output files that contain
# only one line.
#
for outputfile in $outputdir/*.txt ; do
    linecount=$(wc -l "$outputfile" | sed 's/ .*//')  # count lines in it
    if  [ "$linecount" = "1" ] ; then                 # if only one line
        rm "$outputfile"                              # remove the file
    fi
done

# Print the final result
#
for outputfile in $outputdir/*.txt ; do
    cat "$outputfile"
    echo               # empty line to separate groups of same file names
done
