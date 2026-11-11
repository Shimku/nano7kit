#!/bin/sh
# nano7kit, MIT
# build the host harness: sh host/build.sh OUT(recomp dir)
set -e
O=${1:-/tmp/about}; H=$(dirname "$0"); R=$H/../rt; N=$H/../nano
CF="-O1 -g -DRT_CHECK -DNOTICE_NO_GL -I$R -I$O -I$H -I$N -w -fno-strict-aliasing -fwrapv"
mkdir -p $O/obj
for f in $O/recomp_*.c; do b=$(basename $f .c); [ $O/obj/$b.o -nt $f ] || echo "gcc $CF -c $f -o $O/obj/$b.o"; done | xargs -P8 -I{} sh -c '{}'
gcc $CF -o $O/abhost $O/obj/*.o $R/rt_core.c $R/rt_imports.c $R/fngl.c $R/ab_entry.c $H/be_soft.c $H/main.c $N/notice.c -lm -rdynamic
echo built $O/abhost
