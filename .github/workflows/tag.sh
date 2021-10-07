#!/bin/bash

set -eo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
source ${SCRIPT_DIR}/base.sh $*

export CONDA_BUILD_YML=$1

conda activate base
conda build -m .ci_support/${CONDA_BUILD_YML}.yaml conda.recipe
