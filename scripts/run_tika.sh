#!/bin/bash

work_path=$(pwd)

cd $work_path

export TIKA_SERVER_JAR="file://$work_path/tika-server-standard-2.6.0.jar"
nohup java -jar tika-server-standard-2.6.0.jar &