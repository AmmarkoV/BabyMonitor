#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

source venv/bin/activate

python3 babyMonitor.py /dev/video0 8090&
python3 babyMonitor.py /dev/video1 8093 

python3 portal.py --ip 192.168.1.12 -p 8080 -d 8090 -d 8093


exit 0

