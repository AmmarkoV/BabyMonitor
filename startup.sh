#!/bin/bash


DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

source venv/bin/activate

python3 dual.py /dev/video0 8080&
python3 dual.py /dev/video1 8090


#python3 combined4.py

exit 0

