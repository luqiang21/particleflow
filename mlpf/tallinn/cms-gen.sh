#!/bin/bash
#SBATCH -p gpu
#SBATCH --gpus 4
#SBATCH --mem-per-gpu=8G

IMG=/home/software/singularity/tf26.simg:latest
cd ~/particleflow

#TF training
PYTHONPATH=hep_tfds singularity exec --nv $IMG python3 mlpf/pipeline.py train -c parameters/cms-gen.yaml --plot-freq 1
