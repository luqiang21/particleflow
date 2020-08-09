import os
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--start", type=int, default=1, help="start index")
parser.add_argument("--stop", type=int, default=10, help="stop index")
args = parser.parse_args()

for item in range(args.start,args.stop):
    all_done = all([os.path.isfile('/particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/raw/pfntuple_{}_{}.pkl'.format(item, j)) for j in range(0,5)])
    if all_done:
        print('{} all done'.format(item))
    else:
        print('{} not done'.format(item))
        os.system('python /opt/repo/particleflow/test/postprocessing2.py --input /particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/pfntuple_{}.root  --events-per-file 1 --save-normalized-table --outpath /particleflowvol/TTbar_14TeV_TuneCUETP8M1_cfi/raw'.format(item))
