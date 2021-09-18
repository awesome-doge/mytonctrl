set -e
# Проверить sudo
if [ "$(id -u)" != "0" ]; then
	echo "Please run script as root"
	exit 1
fi
# 安裝 依賴套件
apt install -y build-essential git make cmake clang libgflags-dev zlib1g-dev libssl-dev libreadline-dev libmicrohttpd-dev pkg-config libgsl-dev python3 python3-dev python3-pip
pip3 install psutil crc16 requests
# 移除舊的
rm -rf /usr/bin/ton/
rm -rf /usr/src/ton/
rm -rf /usr/src/pow-miner-gpu/
# 下載 pow-miner-gpu
cd /usr/src
git clone --recursive https://github.com/tontechio/pow-miner-gpu.git
cd /usr/src/pow-miner-gpu
git checkout main
cd /usr/src/
mv pow-miner-gpu ton
# 初始化編譯環境
mkdir /usr/bin/ton
cd /usr/bin/ton
export CCACHE_DISABLE=1
export CUDA_HOME=/usr/local/cuda
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64
export PATH=$PATH:$CUDA_HOME/bin
cmake -DCMAKE_BUILD_TYPE=Release -DMINERCUDA=true /usr/src/ton
# 計算需要的cpu
memory=$(cat /proc/meminfo | grep MemAvailable | awk '{print $2}')
let "cpuNumber = memory / 2100000"
# 編譯
make -j ${cpuNumber} pow-miner-cuda lite-client
cd ~