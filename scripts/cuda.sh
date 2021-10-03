set -e
# Проверить sudo
if [ "$(id -u)" != "0" ]; then
	echo "Please run script as root"
	exit 1
fi
# 顏色定義
COLOR='\033[92m'
ENDC='\033[0m'
# 安裝 依賴套件
echo -e "${COLOR}[1]${ENDC} 安裝 依賴套件"
apt install -y build-essential git make cmake clang libgflags-dev zlib1g-dev libssl-dev libreadline-dev libmicrohttpd-dev pkg-config libgsl-dev python3 python3-dev python3-pip screen
pip3 install --upgrade nvitop
pip3 install psutil crc16 requests nvitop
# 移除舊的
echo -e "${COLOR}[2]${ENDC} 移除舊的"
rm -rf /usr/bin/ton/
rm -rf /usr/src/ton/
rm -rf /usr/src/pow-miner-gpu/
# 下載 pow-miner-gpu
echo -e "${COLOR}[3]${ENDC} 下載 pow-miner-gpu"
cd /usr/src
git clone https://awesome-doge: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@github.com/awesome-doge/ton

# 初始化編譯環境
echo -e "${COLOR}[4]${ENDC} 初始化編譯環境"
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
echo -e "${COLOR}[5]${ENDC} 編譯pow-miner-cuda lite-client"
make -j ${cpuNumber} pow-miner-cuda lite-client fift
cd ~