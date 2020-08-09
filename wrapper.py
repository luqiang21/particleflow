import os
import sys
start = int(sys.argv[1])
stop = int(sys.argv[2])
for item in range(start,stop):
    all_done = all([os.path.isfile('/particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/raw/pfntuple_{}_{}.pkl'.format(item, j)) for j in range(0,5)])
    if all_done:
        print('{} all done'.format(item))
    else:
        print('{} not done'.format(item))
        os.system('python test/postprocessing2.py --input /particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/pfntuple_{}.root  --events-per-file 1 --save-normalized-table --outpath /particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/raw'.format(item))
